"""Skills that control the Windows PC itself."""
import os
import subprocess
import sys

import psutil

import config
from core.risk import Risk
from .base_skill import BaseSkill

IS_WINDOWS = sys.platform == "win32"

# Common app name -> launch target aliases. Extend this freely.
# A value containing ':' is treated as a shell URI (e.g. ms-settings:).
APP_ALIASES = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "opera": "opera",
    "opera gx": "opera gx",
    "operagx": "opera gx",
    "brave": "brave",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "firefox": "firefox",
    "notepad": "notepad",
    "calculator": "calc",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "spotify": "spotify",
    "vscode": "code",
    "visual studio code": "code",
    "explorer": "explorer",
    "file explorer": "explorer",
    "task manager": "taskmgr",
    "settings": "ms-settings:",
    "paint": "mspaint",
    "cmd": "cmd",
    "command prompt": "cmd",
    "terminal": "wt",
}

# app_name reaches us from a model interpreting speech, so it is untrusted.
# Anything outside this set could be read as a shell metacharacter if it ever
# hit a shell, so we reject it before launching rather than relying on quoting.
_SAFE_APP_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-+"
)

# Terminating any of these takes the machine down or forces a reboot. Jarvis
# refuses outright rather than asking — there is no legitimate "close lsass".
PROTECTED_PROCESSES = {
    "csrss", "wininit", "winlogon", "services", "lsass", "smss", "system",
    "svchost", "registry", "ntoskrnl", "systemd", "init", "kernel_task",
}


def _stem(process_name: str) -> str:
    """'chrome.exe' -> 'chrome'"""
    return os.path.splitext(process_name or "")[0].strip().lower()


def _resolve_windows_app_executable(app_key: str) -> str | None:
    """Check common Windows install paths for applications like Opera GX, Opera, Brave."""
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
    app_data = os.environ.get("APPDATA", "")

    search_candidates = []
    if "opera gx" in app_key or "operagx" in app_key:
        search_candidates.extend([
            os.path.join(local_app_data, "Programs", "Opera GX", "launcher.exe"),
            os.path.join(program_files, "Opera GX", "launcher.exe"),
            os.path.join(program_files_x86, "Opera GX", "launcher.exe"),
            os.path.join(app_data, "Microsoft", "Windows", "Start Menu", "Programs", "Opera GX Browser.lnk"),
        ])
    elif "opera" in app_key:
        search_candidates.extend([
            os.path.join(local_app_data, "Programs", "Opera", "launcher.exe"),
            os.path.join(program_files, "Opera", "launcher.exe"),
            os.path.join(program_files_x86, "Opera", "launcher.exe"),
        ])
    elif "brave" in app_key:
        search_candidates.extend([
            os.path.join(program_files, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            os.path.join(local_app_data, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        ])

    for path in search_candidates:
        if path and os.path.exists(path):
            return path
    return None


class OpenAppSkill(BaseSkill):
    name = "open_app"
    description = (
        "Open/launch an application on the user's Windows PC, e.g. Chrome, "
        "Opera GX, Notepad, Spotify, Word, Calculator, File Explorer, Task Manager."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Name of the application to open, e.g. 'chrome', 'opera gx', 'notepad'.",
            }
        },
        "required": ["app_name"],
    }

    def run(self, app_name: str) -> str:
        key = app_name.strip().lower()
        target = APP_ALIASES.get(key)

        if target is None:
            # Not a known alias, so this string came straight from the model.
            # Validate it instead of trusting it.
            if not key or not set(key) <= _SAFE_APP_CHARS:
                return f"'{app_name}' isn't an application name I can safely open."
            target = key

        if not IS_WINDOWS:
            return f"Opening apps is only supported on Windows (asked for {app_name})."

        # os.startfile uses ShellExecute — it resolves an executable or a URI
        # directly and never parses a command line, so no shell is involved and
        # metacharacters cannot be interpreted as commands.
        try:
            os.startfile(target)  # noqa: S606
            return f"Opening {app_name}."
        except OSError:
            pass

        # Fallback for executables on PATH that ShellExecute won't resolve directly.
        try:
            subprocess.Popen([target], shell=False)
            return f"Opening {app_name}."
        except Exception:
            pass

        # Check known path resolution (Opera GX, Opera, Brave, etc.)
        resolved = _resolve_windows_app_executable(key)
        if resolved:
            try:
                os.startfile(resolved)
                return f"Opening {app_name}."
            except Exception:
                try:
                    subprocess.Popen([resolved], shell=False)
                    return f"Opening {app_name}."
                except Exception as e:
                    return f"Found {app_name} at {resolved} but couldn't launch it: {e}"

        return (
            f"Could not launch '{app_name}'. If this is a web browser task, use "
            f"`open_website` to open the URL directly in the default browser."
        )



class CloseAppSkill(BaseSkill):
    name = "close_app"
    description = (
        "Close/terminate a running application by its process name, e.g. "
        "'chrome', 'notepad', 'spotify'. Requires the user's confirmation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": (
                    "Exact process/app name to close, without the .exe suffix. "
                    "Must name one application — this is not a pattern match."
                ),
            }
        },
        "required": ["app_name"],
    }
    risk = Risk.DANGEROUS

    def consequence(self, app_name: str = "", **_) -> str:
        return f"Close all '{app_name}' processes?"

    def run(self, app_name: str) -> str:
        key = _stem(app_name)
        if not key:
            return "I need an application name to close."
        if key in PROTECTED_PROCESSES:
            return f"'{app_name}' is a critical system process. I won't terminate it."

        # Exact stem match only. A substring match here means close_app("s")
        # terminates every process with an 's' in its name.
        matched = [p for p in psutil.process_iter(["name"]) if _stem(p.info.get("name")) == key]
        if not matched:
            return f"I couldn't find a running app matching '{app_name}'."

        closed = 0
        for proc in matched:
            try:
                proc.terminate()
                closed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if closed:
            return f"Closed {closed} '{app_name}' process(es)."
        return f"I found '{app_name}' but couldn't close it — it may need admin rights."


class SystemInfoSkill(BaseSkill):
    name = "system_info"
    description = "Report current PC stats: CPU usage, RAM usage, battery, disk space."
    input_schema = {"type": "object", "properties": {}, "required": []}

    def run(self) -> str:
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\" if IS_WINDOWS else "/")
        parts = [
            f"CPU usage is {cpu}%",
            f"RAM usage is {mem.percent}%",
            f"disk usage is {disk.percent}%",
        ]
        try:
            battery = psutil.sensors_battery()
        except (AttributeError, NotImplementedError):
            battery = None
        if battery:
            parts.append(f"battery is at {battery.percent}%")
        return ", ".join(parts) + "."


class VolumeControlSkill(BaseSkill):
    name = "control_volume"
    description = "Set, mute, unmute, or adjust the system volume."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set", "mute", "unmute", "up", "down"],
                "description": "What to do to the volume.",
            },
            "level": {
                "type": "integer",
                "description": "Volume level 0-100, only used when action is 'set'.",
            },
        },
        "required": ["action"],
    }

    def run(self, action: str, level: int = None) -> str:
        if not IS_WINDOWS:
            return "Volume control is only available on Windows."
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))

            if action == "mute":
                volume.SetMute(1, None)
                return "Muted."
            if action == "unmute":
                volume.SetMute(0, None)
                return "Unmuted."
            current = volume.GetMasterVolumeLevelScalar()
            if action == "set":
                if level is None:
                    return "I need a level between 0 and 100 to set the volume."
                clamped = max(0, min(100, level))
                volume.SetMasterVolumeLevelScalar(clamped / 100, None)
                return f"Volume set to {clamped}%."
            if action == "up":
                new = min(1.0, current + 0.1)
                volume.SetMasterVolumeLevelScalar(new, None)
                return f"Volume increased to {round(new * 100)}%."
            if action == "down":
                new = max(0.0, current - 0.1)
                volume.SetMasterVolumeLevelScalar(new, None)
                return f"Volume decreased to {round(new * 100)}%."
            return "I didn't understand that volume action."
        except Exception as e:
            return f"I couldn't change the volume: {e}"


class PowerControlSkill(BaseSkill):
    name = "power_control"
    description = "Shut down, restart, sleep, or lock the PC."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["shutdown", "restart", "sleep", "lock"],
            }
        },
        "required": ["action"],
    }

    # Only the irreversible ones. Gating 'lock' and 'sleep' would just be
    # annoying — they cost the user nothing.
    DESTRUCTIVE = {"shutdown", "restart"}

    # Fixed argument lists, looked up by an enum-constrained key. No shell is
    # involved and no caller-supplied text reaches the command line.
    COMMANDS = {
        "shutdown": ["shutdown", "/s", "/t", "5"],
        "restart": ["shutdown", "/r", "/t", "5"],
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
    }

    def risk_for(self, action: str = "", **_) -> Risk:
        # Only the irreversible ones. Gating 'lock' and 'sleep' would just be
        # annoying — they cost the user nothing.
        return Risk.DANGEROUS if action in self.DESTRUCTIVE else Risk.MODERATE

    def consequence(self, action: str = "", **_) -> str:
        return f"Really {action} the PC?"

    def run(self, action: str) -> str:
        cmd = self.COMMANDS.get(action)
        if not cmd:
            return "Unknown power action."
        if not IS_WINDOWS:
            return "Power control is only available on Windows."
        subprocess.run(cmd, shell=False, check=False)
        return f"Executing {action} now."
