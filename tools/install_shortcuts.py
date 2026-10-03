"""Create (or remove) the "Football Ad Blocker" shortcuts: Desktop + Start menu.

The shortcuts start the app through pythonw.exe - no console window at all - with the project
folder as working directory and assets/app.ico as icon. They carry the same AppUserModelID
the app sets for itself, so a shortcut pinned to the taskbar and the running window share one
button instead of showing up twice.

Windows does not let programs pin themselves: to get it onto the taskbar or the Start tiles,
right-click the Start-menu entry or the desktop shortcut and choose "Pin to taskbar" /
"Pin to Start".

Usage:  python tools\\install_shortcuts.py            create / refresh both
        python tools\\install_shortcuts.py --remove   delete both
"""

from __future__ import annotations

import argparse
import sys
from ctypes import byref
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import comtypes  # noqa: E402
import comtypes.client  # noqa: E402
from comtypes import GUID  # noqa: E402
from comtypes.automation import VT_LPWSTR  # noqa: E402
from comtypes.persist import IPersistFile  # noqa: E402
from comtypes.shelllink import IShellLinkW, ShellLink  # noqa: E402
from pycaw.api.mmdeviceapi.depend import IPropertyStore  # noqa: E402
from pycaw.api.mmdeviceapi.depend.structures import PROPERTYKEY, PROPVARIANT  # noqa: E402

from adblock.app import APP_ID  # noqa: E402

NAME = "Football Ad Blocker"
PKEY_APP_USER_MODEL_ID = (GUID("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"), 5)


def folders() -> dict[str, Path]:
    shell = comtypes.client.CreateObject("WScript.Shell")  # resolves OneDrive-redirected folders too
    return {"Desktop": Path(shell.SpecialFolders("Desktop")), "Start menu": Path(shell.SpecialFolders("Programs"))}


def create(path: Path) -> None:
    link = comtypes.client.CreateObject(ShellLink, interface=IShellLinkW)
    venv, embedded = ROOT / ".venv" / "Scripts" / "pythonw.exe", ROOT / "python" / "pythonw.exe"
    link.SetPath(str(venv if venv.exists() else embedded))  # a clone's venv, or an installed copy's own Python
    link.SetArguments("-m adblock.app")
    link.SetWorkingDirectory(str(ROOT))
    link.SetIconLocation(str(ROOT / "assets" / "app.ico"), 0)
    link.SetDescription("Hides ad breaks on YouTube TV: mutes Chrome and covers the stream until the game is back")

    key = PROPERTYKEY()
    key.fmtid, key.pid = PKEY_APP_USER_MODEL_ID
    value = PROPVARIANT()
    value.vt = VT_LPWSTR
    value.union.pwszVal = APP_ID
    store = link.QueryInterface(IPropertyStore)
    store.SetValue(byref(key), byref(value))
    store.Commit()

    link.QueryInterface(IPersistFile).Save(str(path), True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    for where, folder in folders().items():
        path = folder / f"{NAME}.lnk"
        if args.remove:
            existed = path.exists()
            path.unlink(missing_ok=True)
            print(f"{where}: {'removed' if existed else 'nothing to remove'}  ({path})")
        else:
            create(path)
            print(f"{where}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
