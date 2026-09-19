"""S3 - can we mute only Chrome, cleanly? (uses the production adblock.actuators.audio_mute)

Part 1 (always): find chrome.exe's audio sessions, wait until they are actually producing
sound, then baseline 2 s -> mute 5 s -> restore 2 s, sampling peak meters throughout:
  - Chrome's session meter,
  - the playback device's meter (what reaches the speakers),
  - every other app's mute flag, to prove nothing else was touched.

Part 2 (--loopback): record WASAPI loopback of the default speakers while Chrome's *session*
is muted and then while the *endpoint* (master) is muted, to learn whether loopback still
hears the stream in each case. Only matters for the optional audio-detection signal later.
NOTE: the endpoint part silences the speakers for ~4 s and restores the previous state.

Usage:  python tools\\spike_mute.py [--loopback]
"""

from __future__ import annotations

import argparse
import threading
import time

import _spike
from _spike import OUT, verdict

import numpy as np

from adblock.actuators import audio_mute

SAMPLE_S = 0.1
AUDIBLE = 0.002  # peak-meter value that counts as "sound is playing"


def chrome_peak() -> float:
    return max((s.peak for s in audio_mute.list_sessions()), default=0.0)


def endpoint_meter():
    from pycaw.api.endpointvolume import IAudioMeterInformation
    from pycaw.utils import AudioUtilities
    import comtypes

    speakers = AudioUtilities.GetSpeakers()
    meter = speakers._dev.Activate(IAudioMeterInformation._iid_, comtypes.CLSCTX_ALL, None)
    return speakers, meter.QueryInterface(IAudioMeterInformation)


def sample_phase(name: str, seconds: float, meter, log: list[dict], t0: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        log.append({"t": round(time.perf_counter() - t0, 2), "phase": name,
                    "chrome_peak": chrome_peak(), "endpoint_peak": float(meter.GetPeakValue())})
        time.sleep(SAMPLE_S)


def phase_stats(log: list[dict], phase: str, key: str) -> dict:
    values = [row[key] for row in log if row["phase"] == phase][2:]  # drop the transition samples
    return {"mean": round(float(np.mean(values)), 4), "max": round(float(np.max(values)), 4)} if values else {}


def part1() -> tuple[bool, dict]:
    everything = audio_mute.list_sessions(exe=None)
    print("Audio sessions on active playback devices:")
    for s in everything:
        print(f"  pid={s.pid:<6} {s.exe or '(system sounds)':<28} {s.state:<8} muted={s.muted!s:<5} vol={s.volume:.2f} peak={s.peak:.3f}")
    chrome = [s for s in everything if s.exe == "chrome.exe"]
    if not chrome:
        print("FAIL: chrome.exe has no audio session. Is the stream playing with sound (player not muted)?")
        return False, {"reason": "no chrome.exe session"}

    if not _spike.wait_until(lambda: chrome_peak() > AUDIBLE, 120, "Chrome to produce sound (unmute the player)"):
        print("FAIL: Chrome's session stayed silent - the player is muted or paused.")
        return False, {"reason": "chrome session silent"}

    others_before = {(s.pid, s.device_id): s.muted for s in everything if s.exe != "chrome.exe"}
    original = {(s.pid, s.device_id): s.muted for s in chrome}
    _speakers, meter = endpoint_meter()
    log: list[dict] = []
    t0 = time.perf_counter()
    try:
        sample_phase("baseline", 2.0, meter, log, t0)
        started = time.perf_counter()
        count = audio_mute.set_muted(True)
        mute_call_ms = (time.perf_counter() - started) * 1000
        readback_muted = audio_mute.is_muted()
        print(f"  muted {count} chrome.exe session(s) in {mute_call_ms:.1f} ms; is_muted() -> {readback_muted}")
        sample_phase("muted", 5.0, meter, log, t0)
    finally:
        for s in audio_mute.list_sessions():  # put every session back exactly as it was found
            s.muted = original.get((s.pid, s.device_id), False)
    readback_restored = audio_mute.is_muted()
    print(f"  restored; is_muted() -> {readback_restored}")
    sample_phase("restored", 2.0, meter, log, t0)

    others_after = {(s.pid, s.device_id): s.muted for s in audio_mute.list_sessions(exe=None) if s.exe != "chrome.exe"}
    stats = {p: {k: phase_stats(log, p, k) for k in ("chrome_peak", "endpoint_peak")} for p in ("baseline", "muted", "restored")}
    for phase, values in stats.items():
        print(f"  {phase:>9}: chrome session peak {values['chrome_peak']}   speakers peak {values['endpoint_peak']}")

    baseline = stats["baseline"]["endpoint_peak"].get("mean", 0.0)
    checks = {
        "mute_readback_true": readback_muted is True,
        "restore_readback_matches_original": readback_restored == all(original.values()),
        "speakers_silent_while_muted": stats["muted"]["endpoint_peak"].get("max", 1.0) < max(0.1 * baseline, 0.001),
        "sound_returns_after_restore": stats["restored"]["endpoint_peak"].get("max", 0.0) > AUDIBLE,
        "other_apps_untouched": all(others_after.get(key) == value for key, value in others_before.items()),
    }
    for key, value in checks.items():
        print(f"  {verdict(value)}  {key}")
    if not checks["speakers_silent_while_muted"]:
        print("  (if another app was also playing sound, the speakers legitimately stay live - check the list above)")
    return all(checks.values()), {"mute_call_ms": round(mute_call_ms, 1), "sessions_muted": count, "stats": stats, "checks": checks}


def part2() -> dict:
    """Loopback recording on this thread; the mute choreography on a worker with its own COM apartment."""
    import soundcard

    speaker = soundcard.default_speaker()
    loopback = soundcard.get_microphone(id=str(speaker.name), include_loopback=True)
    print(f"\nLoopback of: {speaker.name}")
    plan = [("normal_1", 3.0), ("session_muted", 4.0), ("normal_2", 2.0), ("endpoint_muted", 4.0), ("normal_3", 2.0)]
    marks: list[tuple[str, float, float]] = []
    done = threading.Event()

    def choreography() -> None:
        import comtypes

        comtypes.CoInitialize()
        speakers, _meter = endpoint_meter()
        endpoint = speakers.EndpointVolume
        endpoint_was_muted = endpoint.GetMute()
        try:
            for name, seconds in plan:
                if name == "session_muted":
                    audio_mute.set_muted(True)
                elif name == "endpoint_muted":
                    endpoint.SetMute(1, None)
                start = time.perf_counter()
                time.sleep(seconds)
                marks.append((name, start, time.perf_counter()))
                audio_mute.set_muted(False)
                endpoint.SetMute(endpoint_was_muted, None)
        finally:
            audio_mute.set_muted(False)
            endpoint.SetMute(endpoint_was_muted, None)
            done.set()

    chunks: list[tuple[float, float, int]] = []  # (time, rms, frames)
    recorded = []
    with loopback.recorder(samplerate=48000, channels=2, blocksize=2400) as recorder:
        worker = threading.Thread(target=choreography, daemon=True)
        worker.start()
        while not done.is_set():
            data = recorder.record(numframes=2400)  # 50 ms
            chunks.append((time.perf_counter(), float(np.sqrt(np.mean(np.square(data)))) if len(data) else 0.0, len(data)))
            recorded.append(data)
        worker.join()

    import wave

    pcm = (np.clip(np.concatenate(recorded), -1, 1) * 32767).astype(np.int16)
    with wave.open(str(OUT / "s3_loopback.wav"), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(pcm.tobytes())

    result = {}
    for name, start, end in marks:
        rms = [r for t, r, _ in chunks if start + 0.4 <= t <= end - 0.1]
        result[name] = {"chunks": len(rms), "rms_mean": round(float(np.mean(rms)), 5) if rms else None}
        print(f"  {name:>15}: {result[name]}")
    normal = np.mean([result[n]["rms_mean"] or 0.0 for n in ("normal_1", "normal_2", "normal_3")])
    for name in ("session_muted", "endpoint_muted"):
        level = result[name]["rms_mean"]
        hears = level is not None and level > 0.1 * normal
        result[f"loopback_hears_stream_during_{name}"] = bool(hears)
        print(f"  loopback hears the stream during {name}: {hears}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--loopback", action="store_true", help="also run the loopback-while-muted experiment")
    args = parser.parse_args()

    ok, report = part1()
    if ok and args.loopback:
        try:
            report["loopback"] = part2()
        except Exception as exc:  # informational experiment: never let it change the S3 verdict
            report["loopback"] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"  loopback experiment failed: {report['loopback']['error']}")
            audio_mute.set_muted(False)
    report["ok"] = ok
    _spike.write_json("s3_mute.json", report)
    print(f"\nS3 {verdict(ok)}   evidence: {OUT}\\s3_mute.json")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
