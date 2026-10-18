import os
import sys
import winreg

APP_NAME = "AutoExport"
EXE = sys.executable
GUI = os.path.abspath(os.path.join(os.path.dirname(__file__), "GUI.py"))
COMMAND = f'"{EXE}" "{GUI}" "%1"'

TARGETS = [
    r"Software\Classes\SystemFileAssociations\.als\shell\AutoExport",
    r"Software\Classes\Directory\shell\AutoExport",
    r"Software\Classes\Directory\Background\shell\AutoExport",
]


def install():
    for key_path in TARGETS:
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, EXE)
            winreg.CloseKey(key)

            cmd_key = winreg.CreateKey(
                winreg.HKEY_CURRENT_USER, key_path + r"\command"
            )
            winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, COMMAND)
            winreg.CloseKey(cmd_key)
            print(f"Added: {key_path}")
        except OSError as e:
            print(f"Failed on {key_path}: {e}")


def uninstall():
    for key_path in TARGETS:
        for sub in (r"\command", ""):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path + sub)
                print(f"Removed: {key_path}{sub}")
            except FileNotFoundError:
                pass
            except OSError as e:
                print(f"Failed on {key_path}{sub}: {e}")


if __name__ == "__main__":
    if "--remove" in sys.argv:
        uninstall()
    else:
        install()