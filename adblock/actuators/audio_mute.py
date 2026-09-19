"""Mute / unmute one application's audio sessions - the Volume Mixer's per-app mute.

Only ever touches sessions that belong to the named executable (chrome.exe); the endpoint
(master) volume and every other app are left alone.

COM objects are apartment-bound, so nothing here is cached between calls: every call
enumerates the sessions afresh on the calling thread (a few ms). Call ``comtypes.CoInitialize()``
first when using this from a thread other than the one that imported it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import comtypes
from pycaw.api.audioclient import ISimpleAudioVolume
from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
from pycaw.api.endpointvolume import IAudioMeterInformation
from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
from pycaw.constants import DEVICE_STATE, CLSID_MMDeviceEnumerator, EDataFlow

from adblock import win32

SESSION_STATES = {0: "inactive", 1: "active", 2: "expired"}


@dataclass
class Session:
    pid: int
    exe: str
    state: str
    device_id: str
    _control: object = field(repr=False)

    @property
    def _volume(self):
        return self._control.QueryInterface(ISimpleAudioVolume)

    @property
    def muted(self) -> bool:
        return bool(self._volume.GetMute())

    @muted.setter
    def muted(self, value: bool) -> None:
        self._volume.SetMute(1 if value else 0, None)

    @property
    def volume(self) -> float:
        return float(self._volume.GetMasterVolume())

    @property
    def peak(self) -> float:
        """Current peak sample value 0..1 of this session's stream."""
        return float(self._control.QueryInterface(IAudioMeterInformation).GetPeakValue())


def list_sessions(exe: str | None = win32.CHROME_EXE) -> list[Session]:
    """Audio sessions on every active playback device, optionally only those of ``exe``.

    All devices, not just the default one: Chrome can be routed to any output.
    """
    enumerator = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    devices = enumerator.EnumAudioEndpoints(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
    found: list[Session] = []
    for d in range(devices.GetCount()):
        device = devices.Item(d)
        manager = device.Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(IAudioSessionManager2)
        sessions = manager.GetSessionEnumerator()
        for s in range(sessions.GetCount()):
            control = sessions.GetSession(s).QueryInterface(IAudioSessionControl2)
            pid = control.GetProcessId()
            name = win32.process_exe(pid) if pid else ""
            if exe is None or name == exe:
                state = SESSION_STATES.get(control.GetState(), "?")
                found.append(Session(pid, name, state, device.GetId(), control))
    return found


def set_muted(muted: bool, exe: str = win32.CHROME_EXE) -> int:
    """Mute/unmute every session of ``exe``. Returns how many sessions exist (0 = nothing to act on)."""
    sessions = list_sessions(exe)
    for session in sessions:
        if session.muted != muted:
            session.muted = muted
    return len(sessions)


def is_muted(exe: str = win32.CHROME_EXE) -> bool | None:
    """True if every session of ``exe`` is muted, False if any isn't, None if it has no sessions."""
    sessions = list_sessions(exe)
    if not sessions:
        return None
    return all(session.muted for session in sessions)
