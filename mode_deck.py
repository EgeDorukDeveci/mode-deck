r"""
Mode Deck

A preview-first Windows mode switcher for gaming, studying, relaxing, and
custom routines. The application uses only Python's standard library.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


APP_NAME = "Mode Deck"
APP_VERSION = "1.0.7"
FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
LOGS_DIR = DATA_DIR / "logs"
CONFIG_PATH = APP_DIR / "config.json"
CURRENT_SESSION_PATH = SESSIONS_DIR / "current.json"
MINECRAFT_EXE_NAMES = {"java.exe", "javaw.exe"}

CRITICAL_PROCESSES = {
    "applicationframehost",
    "csrss",
    "dwm",
    "explorer",
    "fontdrvhost",
    "lsass",
    "memory compression",
    "msmpeng",
    "registry",
    "searchhost",
    "securityhealthservice",
    "services",
    "sihost",
    "smss",
    "spoolsv",
    "startmenuexperiencehost",
    "svchost",
    "system",
    "system idle process",
    "systemsettings",
    "taskhostw",
    "textinputhost",
    "wininit",
    "winlogon",
    "windowsdefender",
    "wudfhost",
    "vgc",
    "vgk",
    "vgtray",
}

THEMES = {
    "dark": {
        "bg": "#141417",
        "sidebar": "#18181c",
        "panel": "#1d1d22",
        "panel_alt": "#25252b",
        "input": "#111114",
        "line": "#3b3b43",
        "text": "#f2efe7",
        "muted": "#aaa69d",
        "green": "#7ac99b",
        "green_dark": "#1d3d2b",
        "blue": "#69d2e7",
        "blue_dark": "#173d45",
        "amber": "#e8c45f",
        "amber_dark": "#493c19",
        "red": "#ff816f",
        "red_dark": "#4d2723",
        "coral": "#ff816f",
        "cyan": "#69d2e7",
        "gold": "#e8c45f",
        "button": "#29292f",
        "button_hover": "#34343b",
        "disabled": "#2b2b30",
        "disabled_text": "#77777f",
    },
    "light": {
        "bg": "#f4f1ea",
        "sidebar": "#ebe7df",
        "panel": "#fffdf8",
        "panel_alt": "#eeeae2",
        "input": "#faf8f2",
        "line": "#ccc6bb",
        "text": "#242329",
        "muted": "#6d6962",
        "green": "#2f8054",
        "green_dark": "#dcecdf",
        "blue": "#187e92",
        "blue_dark": "#d9eef1",
        "amber": "#80600d",
        "amber_dark": "#f0e6c6",
        "red": "#ad4639",
        "red_dark": "#f4ded9",
        "coral": "#c85645",
        "cyan": "#187e92",
        "gold": "#9a7110",
        "button": "#e6e1d8",
        "button_hover": "#dad4ca",
        "disabled": "#e8e4dc",
        "disabled_text": "#969088",
    },
}

P = THEMES["dark"]


def apply_theme(name: str) -> str:
    global P
    selected = name if name in THEMES else "dark"
    P = THEMES[selected]
    return selected


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def action_id() -> str:
    return uuid.uuid4().hex[:12]


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read {path.name}: {exc}") from exc


def log_event(event: str, details: dict[str, Any] | None = None, log_dir: Path = LOGS_DIR) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"mode_deck_{datetime.now():%Y-%m}.jsonl"
    payload = {"time": now_iso(), "event": event, "details": details or {}}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def powershell(
    script: str,
    *,
    timeout: int = 20,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if environment:
        env.update(environment)
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW,
        env=env,
    )


def decode_windows_output(data: bytes) -> str:
    if not data:
        return ""
    if b"\x00" in data:
        return data.decode("utf-16-le", errors="replace").lstrip("\ufeff")
    return data.decode("utf-8", errors="replace")


def run_wsl(arguments: list[str], timeout: int = 20) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["wsl.exe", *arguments],
        capture_output=True,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def normalize_process_name(value: str) -> str:
    name = Path(str(value).strip()).name.lower()
    return name[:-4] if name.endswith(".exe") else name


def is_process_allowed(value: str) -> bool:
    return normalize_process_name(value) not in CRITICAL_PROCESSES


def process_snapshot() -> list[dict[str, Any]]:
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-Process |
  Select-Object ProcessName,Id,Path,MainWindowHandle,MainWindowTitle,SessionId |
  ConvertTo-Json -Compress
"""
    result = powershell(script, timeout=15)
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    rows = parsed if isinstance(parsed, list) else [parsed]
    return [
        {
            "name": str(row.get("ProcessName") or ""),
            "pid": int(row.get("Id") or 0),
            "path": str(row.get("Path") or ""),
            "command": "",
            "main_window": int(row.get("MainWindowHandle") or 0),
            "window_title": str(row.get("MainWindowTitle") or ""),
            "session_id": int(row.get("SessionId") or 0),
        }
        for row in rows
        if isinstance(row, dict)
    ]


def matching_processes(process_name: str) -> list[dict[str, Any]]:
    wanted = normalize_process_name(process_name)
    return [row for row in process_snapshot() if normalize_process_name(row["name"]) == wanted]


def safe_user_app_processes() -> list[dict[str, Any]]:
    current_name = normalize_process_name(Path(sys.executable).name)
    grouped: dict[str, dict[str, Any]] = {}
    for row in process_snapshot():
        name = normalize_process_name(str(row.get("name") or ""))
        if (
            not name
            or not row.get("main_window")
            or name == current_name
            or name in {"mode deck", "python", "pythonw", "py", "pyw"}
            or not is_process_allowed(name)
        ):
            continue
        path = str(row.get("path") or "")
        key = name.lower()
        existing = grouped.get(key)
        if not existing or (path and not existing.get("path")):
            grouped[key] = {
                "name": name,
                "label": str(row.get("window_title") or name).strip() or name,
                "path": path,
            }
    return sorted(grouped.values(), key=lambda item: str(item["label"]).lower())


def graceful_close(process_name: str) -> bool:
    if not is_process_allowed(process_name):
        raise RuntimeError(f"Mode Deck refuses to close protected process: {process_name}")
    stem = normalize_process_name(process_name).replace("'", "''")
    script = (
        f"$items = Get-Process -Name '{stem}' -ErrorAction SilentlyContinue; "
        "$items | ForEach-Object { [void]$_.CloseMainWindow() }"
    )
    return powershell(script).returncode == 0


def force_close(process_name: str) -> bool:
    if not is_process_allowed(process_name):
        raise RuntimeError(f"Mode Deck refuses to force-close protected process: {process_name}")
    stem = normalize_process_name(process_name)
    result = subprocess.run(
        ["taskkill.exe", "/F", "/IM", f"{stem}.exe"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.returncode == 0


def wait_for_process_exit(process_name: str, seconds: float = 8.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not matching_processes(process_name):
            return True
        time.sleep(0.4)
    return not matching_processes(process_name)


def launch_target(target: str, action_type: str = "app", arguments: str = "") -> None:
    if action_type == "url":
        webbrowser.open(target)
        return
    if action_type == "shell":
        os.startfile(target)
        return
    if action_type in {"folder", "file"}:
        os.startfile(target)
        return
    path = Path(os.path.expandvars(target))
    if not path.exists():
        raise RuntimeError(f"Application not found: {path}")
    command = [str(path)]
    if arguments.strip():
        command.extend(shlex.split(arguments, posix=False))
    subprocess.Popen(command, cwd=str(path.parent), close_fds=True)


def is_launch_running(target: str, process_name: str = "") -> bool:
    expected = normalize_process_name(process_name or Path(os.path.expandvars(target)).name)
    return bool(expected and any(normalize_process_name(row["name"]) == expected for row in process_snapshot()))


def power_plans() -> list[dict[str, str]]:
    result = subprocess.run(
        ["powercfg.exe", "/list"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    plans: list[dict[str, str]] = []
    pattern = re.compile(r"([0-9a-fA-F-]{36})\s+\((.*?)\)\s*(\*)?")
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if match:
            plans.append(
                {
                    "guid": match.group(1).lower(),
                    "name": match.group(2).strip(),
                    "active": bool(match.group(3)),
                }
            )
    return plans


def active_power_plan() -> str:
    return next((plan["guid"] for plan in power_plans() if plan["active"]), "")


def set_power_plan(guid: str) -> None:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", guid):
        raise RuntimeError("Invalid power plan GUID.")
    result = subprocess.run(
        ["powercfg.exe", "/setactive", guid],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Windows could not change the power plan.")


def notification_state() -> dict[str, Any]:
    script = r"""
$path = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings'
$name = 'NOC_GLOBAL_SETTING_TOASTS_ENABLED'
$item = Get-ItemProperty -Path $path -Name $name -ErrorAction SilentlyContinue
if ($null -eq $item) {
  '{"exists":false,"value":null}'
} else {
  @{ exists = $true; value = [int]$item.$name } | ConvertTo-Json -Compress
}
"""
    result = powershell(script)
    try:
        value = json.loads(result.stdout.strip())
        return {"exists": bool(value.get("exists")), "value": value.get("value")}
    except (json.JSONDecodeError, AttributeError):
        return {"exists": False, "value": None}


def set_notifications_muted(muted: bool) -> None:
    value = 0 if muted else 1
    script = rf"""
$path = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings'
New-Item -Path $path -Force | Out-Null
New-ItemProperty -Path $path -Name 'NOC_GLOBAL_SETTING_TOASTS_ENABLED' -Value {value} -PropertyType DWord -Force | Out-Null
"""
    result = powershell(script)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Windows could not change notification settings.")


def restore_notification_state(state: dict[str, Any]) -> None:
    if state.get("exists"):
        set_notifications_muted(int(state.get("value", 1)) == 0)
        return
    script = r"""
$path = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings'
Remove-ItemProperty -Path $path -Name 'NOC_GLOBAL_SETTING_TOASTS_ENABLED' -ErrorAction SilentlyContinue
"""
    result = powershell(script)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Windows could not restore notification settings.")


def wsl_distros() -> list[str]:
    try:
        result = run_wsl(["--list", "--quiet"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    text = decode_windows_output(result.stdout)
    return [line.strip().replace("\x00", "") for line in text.splitlines() if line.strip().replace("\x00", "")]


def stop_wsl(action: str, distro: str = "") -> None:
    if action == "shutdown":
        arguments = ["--shutdown"]
    elif action == "terminate" and distro:
        arguments = ["--terminate", distro]
    else:
        raise RuntimeError("Invalid WSL stop action.")
    result = run_wsl(arguments, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(decode_windows_output(result.stderr).strip() or "WSL could not be stopped.")


def start_wsl_distro(distro: str) -> None:
    if not distro:
        return
    result = run_wsl(["-d", distro, "--", "true"], timeout=30)
    if result.returncode != 0:
        raise RuntimeError(decode_windows_output(result.stderr).strip() or f"Could not start WSL distro {distro}.")


def installed_app_candidates() -> dict[str, dict[str, str]]:
    candidates = {
        "chrome": {
            "label": "Google Chrome",
            "path": r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            "process": "chrome",
        },
        "edge": {
            "label": "Microsoft Edge",
            "path": r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
            "process": "msedge",
        },
        "vscode": {
            "label": "Visual Studio Code",
            "path": r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
            "process": "Code",
        },
        "steam": {
            "label": "Steam",
            "path": r"%ProgramFiles(x86)%\Steam\steam.exe",
            "process": "steam",
        },
        "epic": {
            "label": "Epic Games Launcher",
            "path": r"%ProgramFiles%\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe",
            "process": "EpicGamesLauncher",
        },
        "opera_gx": {
            "label": "Opera GX",
            "path": r"%LOCALAPPDATA%\Programs\Opera GX\opera.exe",
            "process": "opera",
        },
        "opencode": {
            "label": "OpenCode",
            "path": r"%LOCALAPPDATA%\Programs\@opencode-aidesktop\OpenCode.exe",
            "process": "OpenCode",
        },
        "discord": {
            "label": "Discord",
            "path": r"%LOCALAPPDATA%\Discord\Update.exe",
            "process": "Discord",
            "arguments": "--processStart Discord.exe",
        },
        "spotify": {
            "label": "Spotify",
            "path": r"%APPDATA%\Spotify\Spotify.exe",
            "process": "Spotify",
        },
    }
    return {
        key: value
        for key, value in candidates.items()
        if Path(os.path.expandvars(value["path"])).exists()
    }


def close_action(app: dict[str, str], *, enabled: bool = True) -> dict[str, Any]:
    return {
        "id": action_id(),
        "type": "close_app",
        "enabled": enabled,
        "label": app["label"],
        "target": app["process"],
        "launch_path": app.get("path", ""),
        "launch_arguments": app.get("arguments", ""),
        "restore": True,
    }


def launch_action(app: dict[str, str], *, enabled: bool = True) -> dict[str, Any]:
    return {
        "id": action_id(),
        "type": "launch_app",
        "enabled": enabled,
        "label": app["label"],
        "target": app["path"],
        "process": app["process"],
        "arguments": app.get("arguments", ""),
        "restore": False,
    }


def browser_url_action(
    app: dict[str, str],
    label: str,
    url: str,
) -> dict[str, Any]:
    return {
        "id": action_id(),
        "type": "launch_app",
        "enabled": True,
        "label": label,
        "target": app["path"],
        "process": app["process"],
        "arguments": url,
        "restore": False,
    }


def shell_app_action(
    label: str,
    app_id: str,
    process: str,
) -> dict[str, Any]:
    return {
        "id": action_id(),
        "type": "launch_shell",
        "enabled": True,
        "label": label,
        "target": rf"shell:AppsFolder\{app_id}",
        "process": process,
        "arguments": "",
        "restore": False,
    }


def mode_template(mode_id: str, name: str, accent: str) -> dict[str, Any]:
    return {
        "id": mode_id,
        "name": name,
        "accent": accent,
        "builtin": True,
        "close_apps": [],
        "launches": [],
        "system": {
            "power_plan_guid": "",
            "mute_notifications": False,
            "wsl_action": "none",
            "wsl_distro": "",
        },
        "restore": {
            "relaunch_closed_apps": True,
            "restart_wsl": True,
        },
    }


def close_safe_apps_action() -> dict[str, Any]:
    return {
        "id": action_id(),
        "type": "close_safe_apps",
        "enabled": True,
        "label": "Close safe user apps",
        "target": "__safe_user_apps__",
        "launch_path": "",
        "launch_arguments": "",
        "restore": True,
    }


def default_config() -> dict[str, Any]:
    apps = installed_app_candidates()
    gaming = mode_template("gaming", "Gaming", "coral")
    study = mode_template("study", "Study", "cyan")
    chill = mode_template("chill", "Chill", "gold")

    gaming["close_apps"].append(close_safe_apps_action())
    if "steam" in apps:
        gaming["launches"].append(launch_action(apps["steam"]))
    if "opera_gx" in apps:
        gaming["launches"].append(
            browser_url_action(apps["opera_gx"], "Discord Web", "https://discord.com/app")
        )

    for key in ("steam", "epic"):
        if key in apps:
            study["close_apps"].append(close_action(apps[key]))
    if "opera_gx" in apps:
        study["launches"].extend(
            [
                browser_url_action(apps["opera_gx"], "WhatsApp Web", "https://web.whatsapp.com/"),
                browser_url_action(apps["opera_gx"], "YouTube", "https://www.youtube.com/"),
            ]
        )

    chill["name"] = "Vibing"
    for key in ("opencode", "vscode", "spotify"):
        if key in apps:
            chill["launches"].append(launch_action(apps[key]))
    chill["launches"].append(
        shell_app_action("Codex", "OpenAI.Codex_2p2nqsd0c76g0!App", "Codex")
    )
    if "opera_gx" in apps:
        chill["launches"].extend(
            [
                browser_url_action(apps["opera_gx"], "WhatsApp Web", "https://web.whatsapp.com/"),
                browser_url_action(apps["opera_gx"], "YouTube", "https://www.youtube.com/"),
                browser_url_action(apps["opera_gx"], "GitHub", "https://github.com/"),
            ]
        )

    return {
        "version": 7,
        "theme": "dark",
        "suggestions_reviewed": False,
        "selected_mode_id": "gaming",
        "modes": [gaming, study, chill],
    }


def repair_empty_builtin_modes(config: dict[str, Any]) -> bool:
    modes = {
        str(mode.get("id")): mode
        for mode in config.get("modes", [])
        if str(mode.get("id")) in {"gaming", "study", "chill"}
    }
    if set(modes) != {"gaming", "study", "chill"}:
        return False
    enabled_count = sum(
        1
        for mode in modes.values()
        for action in mode.get("close_apps", []) + mode.get("launches", [])
        if action.get("enabled")
    )
    if enabled_count:
        return False

    apps = installed_app_candidates()
    desired = {
        "gaming": {
            "close": ("chrome", "edge", "vscode"),
            "launch": ("steam", "discord"),
        },
        "study": {
            "close": ("steam", "epic"),
            "launch": (),
        },
        "chill": {
            "close": ("vscode",),
            "launch": ("spotify", "discord"),
        },
    }
    changed = False
    for mode_id, roles in desired.items():
        mode = modes[mode_id]
        close_items = mode.setdefault("close_apps", [])
        launch_items = mode.setdefault("launches", [])
        for key in roles["close"]:
            if key not in apps:
                continue
            process = normalize_process_name(apps[key]["process"])
            existing = next(
                (
                    item
                    for item in close_items
                    if normalize_process_name(str(item.get("target") or "")) == process
                ),
                None,
            )
            if existing:
                if not existing.get("enabled"):
                    existing["enabled"] = True
                    changed = True
            else:
                close_items.append(close_action(apps[key]))
                changed = True
        for key in roles["launch"]:
            if key not in apps:
                continue
            process = normalize_process_name(apps[key]["process"])
            existing = next(
                (
                    item
                    for item in launch_items
                    if normalize_process_name(str(item.get("process") or "")) == process
                ),
                None,
            )
            if existing:
                if not existing.get("enabled"):
                    existing["enabled"] = True
                    changed = True
            else:
                launch_items.append(launch_action(apps[key]))
                changed = True
    return changed


def apply_personal_study_preset(config: dict[str, Any]) -> bool:
    study = next(
        (mode for mode in config.get("modes", []) if str(mode.get("id")) == "study"),
        None,
    )
    if not study:
        return False
    apps = installed_app_candidates()
    study["close_apps"] = [
        close_action(apps[key])
        for key in ("steam", "epic")
        if key in apps
    ]
    study["launches"] = []
    if "opera_gx" in apps:
        study["launches"] = [
            browser_url_action(apps["opera_gx"], "WhatsApp Web", "https://web.whatsapp.com/"),
            browser_url_action(apps["opera_gx"], "YouTube", "https://www.youtube.com/"),
        ]
    return True


def apply_safe_gaming_preset(config: dict[str, Any]) -> bool:
    gaming = next(
        (mode for mode in config.get("modes", []) if str(mode.get("id")) == "gaming"),
        None,
    )
    if not gaming:
        return False
    gaming["close_apps"] = [close_safe_apps_action()]
    return True


def apply_gaming_browser_preset(config: dict[str, Any]) -> bool:
    gaming = next(
        (mode for mode in config.get("modes", []) if str(mode.get("id")) == "gaming"),
        None,
    )
    if not gaming:
        return False
    apps = installed_app_candidates()
    gaming["launches"] = []
    if "steam" in apps:
        gaming["launches"].append(launch_action(apps["steam"]))
    if "opera_gx" in apps:
        gaming["launches"].append(
            browser_url_action(apps["opera_gx"], "Discord Web", "https://discord.com/app")
        )
    return True


def apply_vibing_preset(config: dict[str, Any]) -> bool:
    vibing = next(
        (mode for mode in config.get("modes", []) if str(mode.get("id")) == "chill"),
        None,
    )
    if not vibing:
        return False
    apps = installed_app_candidates()
    vibing["name"] = "Vibing"
    vibing["close_apps"] = []
    vibing["launches"] = [
        launch_action(apps[key])
        for key in ("opencode", "vscode", "spotify")
        if key in apps
    ]
    vibing["launches"].append(
        shell_app_action("Codex", "OpenAI.Codex_2p2nqsd0c76g0!App", "Codex")
    )
    if "opera_gx" in apps:
        vibing["launches"].extend(
            [
                browser_url_action(apps["opera_gx"], "WhatsApp Web", "https://web.whatsapp.com/"),
                browser_url_action(apps["opera_gx"], "YouTube", "https://www.youtube.com/"),
                browser_url_action(apps["opera_gx"], "GitHub", "https://github.com/"),
            ]
        )
    return True


class ConfigStore:
    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            config = default_config()
            atomic_write_json(self.path, config)
            return config
        config = read_json(self.path, {})
        if not isinstance(config, dict):
            raise RuntimeError("config.json must contain an object.")
        changed = False
        if config.get("theme") not in THEMES:
            config["theme"] = "dark"
            changed = True
        if not isinstance(config.get("modes"), list):
            config["modes"] = default_config()["modes"]
            changed = True
        if int(config.get("version") or 1) < 2:
            builtin_accents = {"gaming": "coral", "study": "cyan", "chill": "gold"}
            for mode in config.get("modes", []):
                mode_id = str(mode.get("id") or "")
                if mode_id in builtin_accents:
                    mode["accent"] = builtin_accents[mode_id]
            config["version"] = 2
            changed = True
        if int(config.get("version") or 1) < 3:
            if repair_empty_builtin_modes(config):
                changed = True
            config["version"] = 3
            changed = True
        if int(config.get("version") or 1) < 4:
            if apply_personal_study_preset(config):
                changed = True
            config["version"] = 4
            changed = True
        if int(config.get("version") or 1) < 5:
            if apply_safe_gaming_preset(config):
                changed = True
            config["version"] = 5
            changed = True
        if int(config.get("version") or 1) < 6:
            if apply_vibing_preset(config):
                changed = True
            config["version"] = 6
            changed = True
        if int(config.get("version") or 1) < 7:
            if apply_gaming_browser_preset(config):
                changed = True
            config["version"] = 7
            changed = True
        known = {str(mode.get("id")) for mode in config["modes"]}
        if config.get("selected_mode_id") not in known and known:
            config["selected_mode_id"] = next(iter(known))
            changed = True
        if changed:
            self.save(config)
        return config

    def save(self, config: dict[str, Any]) -> None:
        atomic_write_json(self.path, config)


@dataclass
class PreviewItem:
    id: str
    group: str
    label: str
    detail: str
    disruptive: bool = False


class ModeEngine:
    def __init__(
        self,
        store: ConfigStore | None = None,
        session_path: Path = CURRENT_SESSION_PATH,
        log_dir: Path = LOGS_DIR,
    ) -> None:
        self.store = store or ConfigStore()
        self.session_path = session_path
        self.log_dir = log_dir
        self.config = self.store.load()

    def save(self) -> None:
        self.store.save(self.config)

    def modes(self) -> list[dict[str, Any]]:
        return self.config.setdefault("modes", [])

    def mode(self, mode_id: str) -> dict[str, Any]:
        for mode in self.modes():
            if str(mode.get("id")) == mode_id:
                return mode
        raise RuntimeError(f"Mode not found: {mode_id}")

    def pending_session(self) -> dict[str, Any] | None:
        value = read_json(self.session_path, None)
        return value if isinstance(value, dict) else None

    def preview(self, mode_id: str) -> list[PreviewItem]:
        mode = self.mode(mode_id)
        running = {normalize_process_name(row["name"]) for row in process_snapshot()}
        items: list[PreviewItem] = []
        for action in mode.get("close_apps", []):
            if not action.get("enabled"):
                continue
            if action.get("type") == "close_safe_apps":
                apps = safe_user_app_processes()
                if not apps:
                    items.append(
                        PreviewItem(
                            f"{action['id']}:none",
                            "Close",
                            "Safe user applications",
                            "No safe visible applications are currently open",
                            True,
                        )
                    )
                for app in apps:
                    items.append(
                        PreviewItem(
                            f"{action['id']}:{app['name']}",
                            "Close",
                            str(app["label"]),
                            f"Process: {app['name']}",
                            True,
                        )
                    )
                continue
            target = str(action.get("target") or "")
            state = "running" if normalize_process_name(target) in running else "not running; will skip"
            items.append(
                PreviewItem(
                    str(action["id"]),
                    "Close",
                    str(action.get("label") or target),
                    state,
                    True,
                )
            )
        for action in mode.get("launches", []):
            if not action.get("enabled"):
                continue
            target = str(action.get("target") or "")
            action_type = str(action.get("type") or "launch_app")
            duplicate = action_type in {"launch_app", "launch_shell"} and is_launch_running(
                target, str(action.get("process") or "")
            ) and not str(action.get("arguments") or "").strip()
            detail = "already running; will skip" if duplicate else target
            items.append(
                PreviewItem(
                    str(action["id"]),
                    "Open",
                    str(action.get("label") or target),
                    detail,
                )
            )
        system = mode.get("system", {})
        guid = str(system.get("power_plan_guid") or "")
        if guid:
            plan_name = next((item["name"] for item in power_plans() if item["guid"] == guid), guid)
            items.append(PreviewItem("system:power", "System", "Power plan", plan_name))
        if system.get("mute_notifications"):
            items.append(PreviewItem("system:notifications", "System", "Mute notifications", "Until restore"))
        wsl_action = str(system.get("wsl_action") or "none")
        if wsl_action != "none":
            detail = "All WSL distributions" if wsl_action == "shutdown" else str(system.get("wsl_distro") or "")
            items.append(PreviewItem("system:wsl", "System", "Stop WSL", detail, True))
        return items

    def activate(
        self,
        mode_id: str,
        selected_ids: set[str],
        *,
        approve_force_close: Callable[[str], bool],
        approve_wsl: Callable[[str], bool],
    ) -> dict[str, Any]:
        if self.pending_session():
            raise RuntimeError("Restore the current Mode Deck session before activating another mode.")
        mode = self.mode(mode_id)
        session = {
            "version": 1,
            "id": uuid.uuid4().hex,
            "mode_id": mode_id,
            "mode_name": mode.get("name", "Mode"),
            "started_at": now_iso(),
            "status": "activating",
            "previous_power_plan": active_power_plan(),
            "previous_notifications": notification_state(),
            "power_changed": False,
            "notifications_changed": False,
            "closed_apps": [],
            "launched_apps": [],
            "wsl": {"stopped": False, "distro": "", "action": "none"},
            "results": [],
        }
        atomic_write_json(self.session_path, session)
        log_event("activation-started", {"mode": mode_id, "session": session["id"]}, self.log_dir)

        for action in mode.get("close_apps", []):
            if action.get("type") == "close_safe_apps":
                prefix = f"{action['id']}:"
                chosen_names = {
                    item_id[len(prefix) :]
                    for item_id in selected_ids
                    if item_id.startswith(prefix) and not item_id.endswith(":none")
                }
                for app in safe_user_app_processes():
                    if app["name"] not in chosen_names:
                        continue
                    target = str(app["name"])
                    closed_record = {
                        "label": app["label"],
                        "process": target,
                        "path": app.get("path", ""),
                        "arguments": "",
                        "restore": bool(action.get("restore", True)),
                    }
                    try:
                        graceful_close(target)
                        exited = wait_for_process_exit(target)
                        forced = False
                        if not exited and approve_force_close(str(app["label"])):
                            forced = force_close(target)
                            exited = wait_for_process_exit(target, 3)
                        if exited:
                            session["closed_apps"].append(closed_record)
                            session["results"].append(
                                {
                                    "action": action["id"],
                                    "target": target,
                                    "status": "done",
                                    "detail": "forced" if forced else "closed",
                                }
                            )
                        else:
                            session["results"].append(
                                {
                                    "action": action["id"],
                                    "target": target,
                                    "status": "skipped",
                                    "detail": "still running",
                                }
                            )
                    except Exception as exc:
                        session["results"].append(
                            {
                                "action": action["id"],
                                "target": target,
                                "status": "failed",
                                "detail": str(exc),
                            }
                        )
                    atomic_write_json(self.session_path, session)
                continue
            if str(action.get("id")) not in selected_ids:
                continue
            target = str(action.get("target") or "")
            if not is_process_allowed(target):
                session["results"].append({"action": action["id"], "status": "refused", "detail": target})
                atomic_write_json(self.session_path, session)
                continue
            matches = matching_processes(target)
            if not matches:
                session["results"].append({"action": action["id"], "status": "skipped", "detail": "not running"})
                atomic_write_json(self.session_path, session)
                continue
            relaunch = str(action.get("launch_path") or next((row["path"] for row in matches if row["path"]), ""))
            closed_record = {
                "label": action.get("label"),
                "process": target,
                "path": relaunch,
                "arguments": action.get("launch_arguments", ""),
                "restore": bool(action.get("restore", True)),
            }
            try:
                graceful_close(target)
                exited = wait_for_process_exit(target)
                forced = False
                if not exited and approve_force_close(str(action.get("label") or target)):
                    forced = force_close(target)
                    exited = wait_for_process_exit(target, 3)
                if exited:
                    session["closed_apps"].append(closed_record)
                    session["results"].append(
                        {"action": action["id"], "status": "done", "detail": "forced" if forced else "closed"}
                    )
                else:
                    session["results"].append({"action": action["id"], "status": "skipped", "detail": "still running"})
            except Exception as exc:
                session["results"].append({"action": action["id"], "status": "failed", "detail": str(exc)})
            atomic_write_json(self.session_path, session)

        system = mode.get("system", {})
        if "system:power" in selected_ids and system.get("power_plan_guid"):
            try:
                set_power_plan(str(system["power_plan_guid"]))
                session["power_changed"] = True
                session["results"].append({"action": "system:power", "status": "done"})
            except Exception as exc:
                session["results"].append({"action": "system:power", "status": "failed", "detail": str(exc)})
            atomic_write_json(self.session_path, session)

        if "system:notifications" in selected_ids and system.get("mute_notifications"):
            try:
                set_notifications_muted(True)
                session["notifications_changed"] = True
                session["results"].append({"action": "system:notifications", "status": "done"})
            except Exception as exc:
                session["results"].append(
                    {"action": "system:notifications", "status": "failed", "detail": str(exc)}
                )
            atomic_write_json(self.session_path, session)

        if "system:wsl" in selected_ids and system.get("wsl_action") != "none":
            description = (
                "Shut down all WSL distributions"
                if system.get("wsl_action") == "shutdown"
                else f"Terminate WSL distro {system.get('wsl_distro')}"
            )
            if approve_wsl(description):
                try:
                    stop_wsl(str(system.get("wsl_action")), str(system.get("wsl_distro") or ""))
                    session["wsl"] = {
                        "stopped": True,
                        "distro": str(system.get("wsl_distro") or ""),
                        "action": str(system.get("wsl_action")),
                    }
                    session["results"].append({"action": "system:wsl", "status": "done"})
                except Exception as exc:
                    session["results"].append({"action": "system:wsl", "status": "failed", "detail": str(exc)})
            else:
                session["results"].append({"action": "system:wsl", "status": "skipped", "detail": "not confirmed"})
            atomic_write_json(self.session_path, session)

        for action in mode.get("launches", []):
            if str(action.get("id")) not in selected_ids:
                continue
            target = str(action.get("target") or "")
            action_type = str(action.get("type") or "launch_app")
            try:
                if action_type in {"launch_app", "launch_shell"} and is_launch_running(
                    target, str(action.get("process") or "")
                ) and not str(action.get("arguments") or "").strip():
                    session["results"].append(
                        {"action": action["id"], "status": "skipped", "detail": "already running"}
                    )
                else:
                    launch_target(target, action_type.replace("launch_", ""), str(action.get("arguments") or ""))
                    session["launched_apps"].append(
                        {
                            "label": action.get("label"),
                            "target": target,
                            "type": action_type,
                            "process": action.get("process", ""),
                        }
                    )
                    session["results"].append({"action": action["id"], "status": "done"})
            except Exception as exc:
                session["results"].append({"action": action["id"], "status": "failed", "detail": str(exc)})
            atomic_write_json(self.session_path, session)

        session["status"] = "active"
        session["completed_at"] = now_iso()
        atomic_write_json(self.session_path, session)
        log_event(
            "activation-completed",
            {
                "mode": mode_id,
                "session": session["id"],
                "failures": sum(1 for item in session["results"] if item["status"] == "failed"),
            },
            self.log_dir,
        )
        return session

    def restore(self) -> dict[str, Any]:
        session = self.pending_session()
        if not session:
            raise RuntimeError("There is no Mode Deck session to restore.")
        try:
            mode = self.mode(str(session.get("mode_id"))) if session.get("mode_id") else {}
        except RuntimeError:
            mode = {}
        restore_options = mode.get("restore", {})
        results: list[dict[str, str]] = []
        session["status"] = "restoring"
        atomic_write_json(self.session_path, session)

        previous_power = str(session.get("previous_power_plan") or "")
        if session.get("power_changed") and previous_power:
            try:
                set_power_plan(previous_power)
                results.append({"action": "power", "status": "done"})
            except Exception as exc:
                results.append({"action": "power", "status": "failed", "detail": str(exc)})

        if session.get("notifications_changed"):
            try:
                restore_notification_state(session.get("previous_notifications") or {})
                results.append({"action": "notifications", "status": "done"})
            except Exception as exc:
                results.append({"action": "notifications", "status": "failed", "detail": str(exc)})

        if restore_options.get("relaunch_closed_apps", True):
            for app in session.get("closed_apps", []):
                if not app.get("restore"):
                    continue
                path = str(app.get("path") or "")
                if not path:
                    results.append(
                        {"action": str(app.get("label")), "status": "skipped", "detail": "no launch path"}
                    )
                    continue
                try:
                    if not is_launch_running(path, str(app.get("process") or "")):
                        launch_target(path, "app", str(app.get("arguments") or ""))
                    results.append({"action": str(app.get("label")), "status": "done"})
                except Exception as exc:
                    results.append({"action": str(app.get("label")), "status": "failed", "detail": str(exc)})

        wsl = session.get("wsl") or {}
        distro = str(wsl.get("distro") or "")
        if wsl.get("stopped") and distro and restore_options.get("restart_wsl", True):
            try:
                start_wsl_distro(distro)
                results.append({"action": "wsl", "status": "done"})
            except Exception as exc:
                results.append({"action": "wsl", "status": "failed", "detail": str(exc)})

        failures = [item for item in results if item["status"] == "failed"]
        archive = self.session_path.with_name(f"{session.get('id', 'session')}.json")
        session["status"] = "restored" if not failures else "restored_with_errors"
        session["restored_at"] = now_iso()
        session["restore_results"] = results
        atomic_write_json(archive, session)
        self.session_path.unlink(missing_ok=True)
        log_event(
            "restore-completed",
            {"session": session.get("id"), "failures": len(failures)},
            self.log_dir,
        )
        return session


class PreviewDialog:
    def __init__(self, parent: tk.Widget, mode_name: str, items: list[PreviewItem]) -> None:
        self.selected_ids: set[str] | None = None
        self.window = tk.Toplevel(parent)
        self.window.title(f"Preview {mode_name}")
        self.window.geometry("760x560")
        self.window.minsize(620, 440)
        self.window.configure(bg=P["bg"])
        self.window.transient(parent)
        self.window.grab_set()
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(2, weight=1)

        tk.Label(
            self.window,
            text=f"Activate {mode_name}",
            bg=P["bg"],
            fg=P["text"],
            font=("Segoe UI Semibold", 20),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=26, pady=(22, 4))
        tk.Label(
            self.window,
            text="Review and disable any action you do not want this time.",
            bg=P["bg"],
            fg=P["muted"],
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=26, pady=(0, 14))

        body = tk.Frame(self.window, bg=P["panel"], highlightthickness=1, highlightbackground=P["line"])
        body.grid(row=2, column=0, sticky="nsew", padx=26)
        body.columnconfigure(0, weight=1)
        self.variables: dict[str, tk.BooleanVar] = {}
        canvas = tk.Canvas(body, bg=P["panel"], highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        list_frame = tk.Frame(canvas, bg=P["panel"])
        list_frame.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        body.rowconfigure(0, weight=1)

        if not items:
            tk.Label(
                list_frame,
                text="This mode has no enabled actions.",
                bg=P["panel"],
                fg=P["muted"],
                font=("Segoe UI", 10),
            ).pack(anchor="w", padx=18, pady=18)
        for item in items:
            row = tk.Frame(list_frame, bg=P["panel"])
            row.pack(fill="x", padx=16, pady=(12, 0))
            variable = tk.BooleanVar(value=True)
            self.variables[item.id] = variable
            ttk.Checkbutton(row, variable=variable).pack(side="left", anchor="n", pady=2)
            text = tk.Frame(row, bg=P["panel"])
            text.pack(side="left", fill="x", expand=True, padx=(8, 0))
            tk.Label(
                text,
                text=f"{item.group}  /  {item.label}",
                bg=P["panel"],
                fg=P["red"] if item.disruptive else P["text"],
                font=("Segoe UI Semibold", 10),
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                text,
                text=item.detail,
                bg=P["panel"],
                fg=P["muted"],
                font=("Segoe UI", 9),
                anchor="w",
                wraplength=620,
                justify="left",
            ).pack(fill="x", pady=(2, 0))

        footer = tk.Frame(self.window, bg=P["bg"])
        footer.grid(row=3, column=0, sticky="ew", padx=26, pady=18)
        footer.columnconfigure(0, weight=1)
        ttk.Button(footer, text="Cancel", command=self.window.destroy).grid(row=0, column=1, padx=(8, 8))
        ttk.Button(footer, text="Activate", style="Primary.TButton", command=self._accept).grid(
            row=0, column=2
        )

    def _accept(self) -> None:
        self.selected_ids = {item_id for item_id, variable in self.variables.items() if variable.get()}
        self.window.destroy()

    def show(self) -> set[str] | None:
        self.window.wait_window()
        return self.selected_ids


class ModeDeckApp:
    def __init__(
        self,
        root: tk.Tk,
        *,
        engine: ModeEngine | None = None,
        offer_suggestions: bool = True,
    ) -> None:
        self.root = root
        self.engine = engine or ModeEngine()
        self.config = self.engine.config
        apply_theme(str(self.config.get("theme") or "dark"))
        self.selected_mode_id = str(
            self.config.get("selected_mode_id")
            or (self.engine.modes()[0].get("id") if self.engine.modes() else "")
        )
        self.mode_buttons: dict[str, tk.Button] = {}
        self.power_map: dict[str, str] = {}
        self._build_styles()
        self._build_ui()
        self.refresh_all()
        if offer_suggestions and not self.config.get("suggestions_reviewed"):
            self.root.after(350, self._review_suggestions)
        if self.engine.pending_session():
            self.root.after(500, self._offer_recovery)

    def _build_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TButton",
            font=("Segoe UI Semibold", 10),
            padding=(13, 9),
            background=P["button"],
            foreground=P["text"],
            bordercolor=P["line"],
            lightcolor=P["button"],
            darkcolor=P["button"],
        )
        style.map(
            "TButton",
            background=[("active", P["button_hover"]), ("disabled", P["disabled"])],
            foreground=[("disabled", P["disabled_text"])],
        )
        style.configure(
            "Primary.TButton",
            font=("Segoe UI Semibold", 11),
            padding=(17, 12),
            background=P["coral"],
            foreground="#23100d",
            bordercolor=P["coral"],
            lightcolor=P["coral"],
            darkcolor=P["coral"],
        )
        style.map(
            "Primary.TButton",
            background=[("active", P["coral"]), ("disabled", P["disabled"])],
            foreground=[("disabled", P["disabled_text"])],
        )
        style.configure(
            "Danger.TButton",
            background=P["red_dark"],
            foreground=P["red"],
            bordercolor=P["red"],
            lightcolor=P["red_dark"],
            darkcolor=P["red_dark"],
        )
        style.configure(
            "Compact.TButton",
            font=("Segoe UI Semibold", 9),
            padding=(8, 7),
            background=P["button"],
            foreground=P["text"],
            bordercolor=P["line"],
            lightcolor=P["button"],
            darkcolor=P["button"],
        )
        style.configure(
            "CompactDanger.TButton",
            font=("Segoe UI Semibold", 9),
            padding=(8, 7),
            background=P["red_dark"],
            foreground=P["red"],
            bordercolor=P["red"],
            lightcolor=P["red_dark"],
            darkcolor=P["red_dark"],
        )
        style.configure(
            "TNotebook",
            background=P["bg"],
            borderwidth=0,
        )
        style.configure(
            "TNotebook.Tab",
            font=("Segoe UI Semibold", 9),
            padding=(14, 9),
            background=P["button"],
            foreground=P["muted"],
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", P["panel"])],
            foreground=[("selected", P["text"])],
        )
        for widget in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(
                widget,
                fieldbackground=P["input"],
                foreground=P["text"],
                bordercolor=P["line"],
                arrowcolor=P["text"],
            )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", P["input"])],
            foreground=[("readonly", P["text"])],
            selectbackground=[("readonly", P["input"])],
            selectforeground=[("readonly", P["text"])],
        )
        style.configure(
            "Treeview",
            background=P["input"],
            fieldbackground=P["input"],
            foreground=P["text"],
            rowheight=28,
            bordercolor=P["line"],
        )
        style.configure(
            "Treeview.Heading",
            background=P["panel_alt"],
            foreground=P["muted"],
            font=("Segoe UI Semibold", 9),
        )
        style.map("Treeview", background=[("selected", P["blue_dark"])])

    def _build_ui(self) -> None:
        self.root.title("Mode Deck")
        self.root.geometry("1240x760")
        self.root.minsize(980, 640)
        self.root.configure(bg=P["bg"])
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        header = tk.Frame(self.root, bg=P["bg"], height=68)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.columnconfigure(2, weight=1)

        mark = tk.Canvas(header, width=48, height=30, bg=P["bg"], highlightthickness=0)
        mark.grid(row=0, column=0, padx=(28, 13), pady=19)
        mark.create_rectangle(2, 15, 12, 28, fill=P["coral"], outline="")
        mark.create_rectangle(18, 7, 28, 28, fill=P["cyan"], outline="")
        mark.create_rectangle(34, 2, 44, 28, fill=P["gold"], outline="")
        tk.Label(
            header,
            text="Mode Deck",
            bg=P["bg"],
            fg=P["text"],
            font=("Segoe UI Semibold", 18),
        ).grid(row=0, column=1, sticky="w")
        self.session_badge = tk.Label(
            header,
            text="",
            bg=P["green_dark"],
            fg=P["green"],
            font=("Segoe UI Semibold", 9),
            padx=11,
            pady=7,
        )
        self.session_badge.grid(row=0, column=3, padx=(12, 16))
        self.session_badge.grid_remove()
        tk.Label(
            header,
            text="Theme",
            bg=P["bg"],
            fg=P["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=4, padx=(0, 7))
        self.theme_var = tk.StringVar(value=str(self.config.get("theme") or "dark").title())
        theme = ttk.Combobox(
            header,
            textvariable=self.theme_var,
            values=("Dark", "Light"),
            state="readonly",
            width=8,
        )
        theme.grid(row=0, column=5, padx=(0, 28))
        theme.bind("<<ComboboxSelected>>", self.change_theme)

        shelf = tk.Frame(
            self.root,
            bg=P["sidebar"],
            highlightthickness=1,
            highlightbackground=P["line"],
        )
        shelf.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 18))
        shelf.columnconfigure(0, weight=1)

        list_container = tk.Frame(shelf, bg=P["sidebar"], height=76)
        list_container.grid(row=0, column=0, sticky="ew", padx=(12, 6))
        list_container.grid_propagate(False)
        list_container.columnconfigure(0, weight=1)
        list_container.rowconfigure(0, weight=1)
        self.mode_canvas = tk.Canvas(
            list_container,
            bg=P["sidebar"],
            highlightthickness=0,
            borderwidth=0,
            height=72,
        )
        self.mode_list = tk.Frame(self.mode_canvas, bg=P["sidebar"])
        self.mode_list.bind(
            "<Configure>",
            lambda _event: self.mode_canvas.configure(
                scrollregion=self.mode_canvas.bbox("all")
            ),
        )
        self.mode_window = self.mode_canvas.create_window(
            (0, 0), window=self.mode_list, anchor="nw"
        )
        self.mode_canvas.bind(
            "<Configure>",
            lambda event: self.mode_canvas.itemconfigure(self.mode_window, height=event.height),
        )
        self.mode_canvas.grid(row=0, column=0, sticky="nsew")
        self.mode_canvas.bind("<MouseWheel>", self._scroll_mode_shelf)
        self.mode_list.bind("<MouseWheel>", self._scroll_mode_shelf)

        management = tk.Frame(shelf, bg=P["sidebar"])
        management.grid(row=0, column=1, sticky="ns", padx=(6, 12), pady=10)
        ttk.Button(management, text="New mode", style="Compact.TButton", command=self.create_mode).grid(
            row=0, column=0, columnspan=2, sticky="ew"
        )
        ttk.Button(
            management, text="<", style="Compact.TButton", command=lambda: self.move_mode(-1)
        ).grid(row=1, column=0, sticky="ew", pady=(6, 0), padx=(0, 3))
        ttk.Button(
            management, text=">", style="Compact.TButton", command=lambda: self.move_mode(1)
        ).grid(row=1, column=1, sticky="ew", pady=(6, 0), padx=(3, 0))
        ttk.Button(
            management,
            text="Delete",
            style="CompactDanger.TButton",
            command=self.delete_mode,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        workspace = tk.Frame(self.root, bg=P["bg"])
        workspace.grid(row=2, column=0, sticky="nsew", padx=28, pady=(0, 24))
        workspace.columnconfigure(0, weight=3)
        workspace.columnconfigure(1, weight=1, minsize=300)
        workspace.rowconfigure(0, weight=1)

        editor = tk.Frame(workspace, bg=P["bg"])
        editor.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(2, weight=1)

        heading = tk.Frame(editor, bg=P["bg"])
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        heading.columnconfigure(0, weight=1)
        self.mode_name_label = tk.Label(
            heading,
            text="",
            bg=P["bg"],
            fg=P["text"],
            font=("Segoe UI Semibold", 25),
            anchor="w",
        )
        self.mode_name_label.grid(row=0, column=0, sticky="ew")
        self.save_button = ttk.Button(
            heading, text="Save changes", command=self.save_mode
        )
        self.save_button.grid(row=0, column=1, sticky="e")

        self.summary_label = tk.Label(
            editor,
            text="",
            bg=P["bg"],
            fg=P["muted"],
            font=("Segoe UI", 10),
            anchor="w",
        )
        self.summary_label.grid(row=1, column=0, sticky="ew", pady=(0, 12))

        self.notebook = ttk.Notebook(editor)
        self.notebook.grid(row=2, column=0, sticky="nsew")
        self._build_general_tab()
        self._build_close_tab()
        self._build_launch_tab()
        self._build_system_tab()
        self._build_restore_tab()

        run_sheet = tk.Frame(
            workspace,
            bg=P["panel_alt"],
            highlightthickness=1,
            highlightbackground=P["line"],
            width=310,
        )
        run_sheet.grid(row=0, column=1, sticky="nsew")
        run_sheet.grid_propagate(False)
        run_sheet.columnconfigure(0, weight=1)
        run_sheet.rowconfigure(2, weight=1)
        tk.Label(
            run_sheet,
            text="Activation queue",
            bg=P["panel_alt"],
            fg=P["text"],
            font=("Segoe UI Semibold", 15),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 4))
        tk.Label(
            run_sheet,
            text="Enabled actions for the selected mode",
            bg=P["panel_alt"],
            fg=P["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 12))
        self.queue_list = tk.Listbox(
            run_sheet,
            bg=P["input"],
            fg=P["text"],
            selectbackground=P["blue_dark"],
            selectforeground=P["text"],
            highlightthickness=1,
            highlightbackground=P["line"],
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 9),
            activestyle="none",
        )
        self.queue_list.grid(row=2, column=0, sticky="nsew", padx=18)
        self.restore_button = ttk.Button(
            run_sheet, text="Restore previous state", command=self.restore_session
        )
        self.restore_button.grid(row=3, column=0, sticky="ew", padx=18, pady=(16, 8))
        self.activate_button = ttk.Button(
            run_sheet,
            text="Preview and activate",
            style="Primary.TButton",
            command=self.preview_and_activate,
        )
        self.activate_button.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 18))

    def _tab(self, title: str) -> tk.Frame:
        frame = tk.Frame(self.notebook, bg=P["panel"], padx=18, pady=18)
        self.notebook.add(frame, text=title)
        frame.columnconfigure(0, weight=1)
        return frame

    def _build_general_tab(self) -> None:
        tab = self._tab("General")
        tk.Label(tab, text="Mode name", bg=P["panel"], fg=P["muted"], font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky="w"
        )
        self.name_var = tk.StringVar()
        ttk.Entry(tab, textvariable=self.name_var).grid(row=1, column=0, sticky="ew", pady=(5, 16))
        tk.Label(tab, text="Accent", bg=P["panel"], fg=P["muted"], font=("Segoe UI", 9)).grid(
            row=2, column=0, sticky="w"
        )
        self.accent_var = tk.StringVar()
        ttk.Combobox(
            tab,
            textvariable=self.accent_var,
            values=("coral", "cyan", "gold"),
            state="readonly",
        ).grid(row=3, column=0, sticky="w", pady=(5, 0))

    def _tree_tab(
        self,
        title: str,
        columns: tuple[str, ...],
        headings: tuple[str, ...],
    ) -> tuple[tk.Frame, ttk.Treeview, tk.Frame]:
        tab = self._tab(title)
        tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        for column, heading in zip(columns, headings):
            tree.heading(column, text=heading)
            tree.column(column, width=140 if column != "target" else 330, anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)
        tab.rowconfigure(0, weight=1)
        controls = tk.Frame(tab, bg=P["panel"])
        controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        return tab, tree, controls

    def _build_close_tab(self) -> None:
        _tab, self.close_tree, controls = self._tree_tab(
            "Close Apps",
            ("enabled", "label", "target", "restore"),
            ("On", "Application", "Process", "Restore"),
        )
        ttk.Button(controls, text="Add", command=self.add_close_action).pack(side="left")
        ttk.Button(controls, text="Edit", command=lambda: self.edit_action("close")).pack(
            side="left", padx=(7, 0)
        )
        ttk.Button(controls, text="Toggle", command=lambda: self.toggle_action("close")).pack(
            side="left", padx=(7, 0)
        )
        ttk.Button(controls, text="Remove", command=lambda: self.remove_action("close")).pack(
            side="left", padx=(7, 0)
        )

    def _build_launch_tab(self) -> None:
        _tab, self.launch_tree, controls = self._tree_tab(
            "Launch / Open",
            ("enabled", "label", "kind", "target"),
            ("On", "Name", "Type", "Target"),
        )
        ttk.Button(controls, text="Add app", command=lambda: self.add_launch_action("app")).pack(
            side="left"
        )
        ttk.Button(controls, text="Add website", command=lambda: self.add_launch_action("url")).pack(
            side="left", padx=(7, 0)
        )
        ttk.Button(controls, text="Add file/folder", command=self.add_path_action).pack(
            side="left", padx=(7, 0)
        )
        ttk.Button(controls, text="Edit", command=lambda: self.edit_action("launch")).pack(
            side="left", padx=(7, 0)
        )
        ttk.Button(controls, text="Toggle", command=lambda: self.toggle_action("launch")).pack(
            side="left", padx=(7, 0)
        )
        ttk.Button(controls, text="Remove", command=lambda: self.remove_action("launch")).pack(
            side="left", padx=(7, 0)
        )

    def _build_system_tab(self) -> None:
        tab = self._tab("System")
        plans = power_plans()
        values = ["No change"] + [f"{plan['name']}  [{plan['guid']}]" for plan in plans]
        self.power_map = {values[index + 1]: plan["guid"] for index, plan in enumerate(plans)}
        tk.Label(tab, text="Power plan", bg=P["panel"], fg=P["muted"], font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky="w"
        )
        self.power_var = tk.StringVar(value="No change")
        self.power_picker = ttk.Combobox(
            tab, textvariable=self.power_var, values=tuple(values), state="readonly"
        )
        self.power_picker.grid(row=1, column=0, sticky="ew", pady=(5, 16))
        self.notifications_var = tk.BooleanVar()
        ttk.Checkbutton(tab, text="Mute notifications until restore", variable=self.notifications_var).grid(
            row=2, column=0, sticky="w", pady=(0, 16)
        )
        tk.Label(tab, text="WSL action", bg=P["panel"], fg=P["muted"], font=("Segoe UI", 9)).grid(
            row=3, column=0, sticky="w"
        )
        self.wsl_action_var = tk.StringVar(value="none")
        ttk.Combobox(
            tab,
            textvariable=self.wsl_action_var,
            values=("none", "shutdown", "terminate"),
            state="readonly",
        ).grid(row=4, column=0, sticky="ew", pady=(5, 16))
        tk.Label(tab, text="WSL distribution", bg=P["panel"], fg=P["muted"], font=("Segoe UI", 9)).grid(
            row=5, column=0, sticky="w"
        )
        self.wsl_distro_var = tk.StringVar()
        ttk.Combobox(
            tab,
            textvariable=self.wsl_distro_var,
            values=tuple(wsl_distros()),
            state="readonly",
        ).grid(row=6, column=0, sticky="ew", pady=(5, 0))

    def _build_restore_tab(self) -> None:
        tab = self._tab("Restore")
        self.relaunch_var = tk.BooleanVar(value=True)
        self.restart_wsl_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            tab,
            text="Relaunch applications closed by Mode Deck",
            variable=self.relaunch_var,
        ).grid(row=0, column=0, sticky="w", pady=(0, 14))
        ttk.Checkbutton(
            tab,
            text="Restart the selected WSL distribution",
            variable=self.restart_wsl_var,
        ).grid(row=1, column=0, sticky="w")

    def selected_mode(self) -> dict[str, Any]:
        return self.engine.mode(self.selected_mode_id)

    def refresh_all(self) -> None:
        self.refresh_sidebar()
        self.load_mode()
        self.refresh_session_state()

    def refresh_sidebar(self) -> None:
        for child in self.mode_list.winfo_children():
            child.destroy()
        self.mode_buttons.clear()
        accents = {
            "coral": P["coral"],
            "cyan": P["cyan"],
            "gold": P["gold"],
            "green": P["coral"],
            "blue": P["cyan"],
            "amber": P["gold"],
        }
        for index, mode in enumerate(self.engine.modes()):
            selected = str(mode.get("id")) == self.selected_mode_id
            accent = accents.get(str(mode.get("accent")), P["cyan"])
            action_count = sum(
                1
                for item in mode.get("close_apps", []) + mode.get("launches", [])
                if item.get("enabled")
            )
            row = tk.Frame(
                self.mode_list,
                bg=P["panel_alt"] if selected else P["panel"],
                highlightthickness=1,
                highlightbackground=accent if selected else P["line"],
                width=158,
                height=58,
            )
            row.grid(row=0, column=index, sticky="ns", padx=(0, 8), pady=8)
            row.grid_propagate(False)
            row.columnconfigure(1, weight=1)
            dot = tk.Canvas(row, width=14, height=14, bg=row.cget("bg"), highlightthickness=0)
            dot.grid(row=0, column=0, rowspan=2, padx=(12, 5))
            dot.create_oval(3, 3, 11, 11, fill=accent, outline="")
            button = tk.Button(
                row,
                text=str(mode.get("name") or "Mode"),
                command=lambda value=str(mode.get("id")): self.select_mode(value),
                bg=P["panel_alt"] if selected else P["panel"],
                fg=P["text"],
                activebackground=P["panel_alt"],
                activeforeground=P["text"],
                relief="flat",
                borderwidth=0,
                anchor="w",
                padx=4,
                pady=6,
                font=("Segoe UI Semibold", 10),
                cursor="hand2",
            )
            button.grid(row=0, column=1, sticky="ew")
            meta_label = tk.Label(
                row,
                text=f"{action_count} action{'s' if action_count != 1 else ''}",
                bg=P["panel_alt"] if selected else P["panel"],
                fg=P["muted"],
                font=("Segoe UI", 8),
                anchor="w",
            )
            meta_label.grid(row=1, column=1, sticky="ew", padx=4, pady=(0, 6))
            for widget in (row, dot, button, meta_label):
                widget.bind("<MouseWheel>", self._scroll_mode_shelf)
            self.mode_buttons[str(mode.get("id"))] = button

    def _scroll_mode_shelf(self, event: tk.Event) -> str:
        direction = -1 if event.delta > 0 else 1
        self.mode_canvas.xview_scroll(direction * 3, "units")
        return "break"

    def select_mode(self, mode_id: str) -> None:
        self.save_mode(silent=True)
        self.selected_mode_id = mode_id
        self.config["selected_mode_id"] = mode_id
        self.engine.save()
        self.refresh_all()

    def load_mode(self) -> None:
        mode = self.selected_mode()
        self.mode_name_label.configure(text=str(mode.get("name") or "Mode"))
        self.summary_label.configure(
            text=f"{sum(1 for item in mode.get('close_apps', []) if item.get('enabled'))} close actions  /  "
            f"{sum(1 for item in mode.get('launches', []) if item.get('enabled'))} launch actions"
        )
        self.name_var.set(str(mode.get("name") or ""))
        accent = str(mode.get("accent") or "cyan")
        self.accent_var.set({"green": "coral", "blue": "cyan", "amber": "gold"}.get(accent, accent))
        system = mode.get("system", {})
        guid = str(system.get("power_plan_guid") or "")
        self.power_var.set(next((label for label, value in self.power_map.items() if value == guid), "No change"))
        self.notifications_var.set(bool(system.get("mute_notifications")))
        self.wsl_action_var.set(str(system.get("wsl_action") or "none"))
        self.wsl_distro_var.set(str(system.get("wsl_distro") or ""))
        restore = mode.get("restore", {})
        self.relaunch_var.set(bool(restore.get("relaunch_closed_apps", True)))
        self.restart_wsl_var.set(bool(restore.get("restart_wsl", True)))
        self.refresh_action_trees()
        self.refresh_queue()

    def refresh_action_trees(self) -> None:
        for tree in (self.close_tree, self.launch_tree):
            for item in tree.get_children():
                tree.delete(item)
        mode = self.selected_mode()
        for action in mode.get("close_apps", []):
            target = (
                "Visible user applications"
                if action.get("type") == "close_safe_apps"
                else action.get("target")
            )
            self.close_tree.insert(
                "",
                "end",
                iid=str(action["id"]),
                values=(
                    "Yes" if action.get("enabled") else "No",
                    action.get("label"),
                    target,
                    "Yes" if action.get("restore") else "No",
                ),
            )
        for action in mode.get("launches", []):
            kind = str(action.get("type") or "").replace("launch_", "")
            self.launch_tree.insert(
                "",
                "end",
                iid=str(action["id"]),
                values=(
                    "Yes" if action.get("enabled") else "No",
                    action.get("label"),
                    kind,
                    action.get("target"),
                ),
            )
        self.refresh_queue()

    def refresh_queue(self) -> None:
        self.queue_list.delete(0, tk.END)
        mode = self.selected_mode()
        for action in mode.get("close_apps", []):
            if action.get("enabled"):
                self.queue_list.insert(tk.END, f"CLOSE   {action.get('label')}")
        for action in mode.get("launches", []):
            if action.get("enabled"):
                kind = str(action.get("type") or "launch").replace("launch_", "").upper()
                self.queue_list.insert(tk.END, f"{kind:<7} {action.get('label')}")
        system = mode.get("system", {})
        if system.get("power_plan_guid"):
            self.queue_list.insert(tk.END, "SYSTEM  Power plan")
        if system.get("mute_notifications"):
            self.queue_list.insert(tk.END, "SYSTEM  Mute notifications")
        if system.get("wsl_action") != "none":
            self.queue_list.insert(tk.END, "SYSTEM  Stop WSL")
        if self.queue_list.size() == 0:
            self.queue_list.insert(tk.END, "No enabled actions")

    def save_mode(self, silent: bool = False) -> None:
        mode = self.selected_mode()
        name = self.name_var.get().strip()
        if not name:
            if not silent:
                messagebox.showwarning("Mode name", "Enter a mode name.", parent=self.root)
            return
        mode["name"] = name
        mode["accent"] = self.accent_var.get() or "cyan"
        system = mode.setdefault("system", {})
        system["power_plan_guid"] = self.power_map.get(self.power_var.get(), "")
        system["mute_notifications"] = bool(self.notifications_var.get())
        system["wsl_action"] = self.wsl_action_var.get() or "none"
        system["wsl_distro"] = self.wsl_distro_var.get().strip()
        restore = mode.setdefault("restore", {})
        restore["relaunch_closed_apps"] = bool(self.relaunch_var.get())
        restore["restart_wsl"] = bool(self.restart_wsl_var.get())
        self.engine.save()
        self.mode_name_label.configure(text=name)
        self.refresh_sidebar()
        self.refresh_queue()
        if not silent:
            messagebox.showinfo("Mode saved", f"{name} was saved.", parent=self.root)

    def add_close_action(self) -> None:
        process = simpledialog.askstring(
            "Close application",
            "Process name, for example chrome or Code:",
            parent=self.root,
        )
        if not process:
            return
        process = normalize_process_name(process)
        if not is_process_allowed(process):
            messagebox.showerror(
                "Protected process",
                "Mode Deck refuses to add that Windows-critical process.",
                parent=self.root,
            )
            return
        label = simpledialog.askstring(
            "Application name", "Display name:", initialvalue=process, parent=self.root
        )
        if not label:
            return
        launch_path = filedialog.askopenfilename(
            title="Optional: choose executable for restoration",
            filetypes=(("Applications", "*.exe"), ("All files", "*.*")),
            parent=self.root,
        )
        self.selected_mode().setdefault("close_apps", []).append(
            {
                "id": action_id(),
                "type": "close_app",
                "enabled": True,
                "label": label,
                "target": process,
                "launch_path": launch_path,
                "launch_arguments": "",
                "restore": True,
            }
        )
        self.engine.save()
        self.refresh_action_trees()

    def add_launch_action(self, kind: str) -> None:
        if kind == "app":
            target = filedialog.askopenfilename(
                title="Choose application",
                filetypes=(("Applications", "*.exe"), ("All files", "*.*")),
                parent=self.root,
            )
            if not target:
                return
            label_default = Path(target).stem
        else:
            target = simpledialog.askstring("Open website", "Website URL:", parent=self.root)
            if not target:
                return
            if not urllib.parse.urlparse(target).scheme:
                target = "https://" + target
            label_default = urllib.parse.urlparse(target).netloc or "Website"
        label = simpledialog.askstring(
            "Action name", "Display name:", initialvalue=label_default, parent=self.root
        )
        if not label:
            return
        self.selected_mode().setdefault("launches", []).append(
            {
                "id": action_id(),
                "type": f"launch_{kind}",
                "enabled": True,
                "label": label,
                "target": target,
                "process": Path(target).stem if kind == "app" else "",
                "arguments": "",
                "restore": False,
            }
        )
        self.engine.save()
        self.refresh_action_trees()

    def add_path_action(self) -> None:
        target = filedialog.askopenfilename(title="Choose file", parent=self.root)
        kind = "file"
        if not target:
            target = filedialog.askdirectory(title="Choose folder", parent=self.root)
            kind = "folder"
        if not target:
            return
        self.selected_mode().setdefault("launches", []).append(
            {
                "id": action_id(),
                "type": f"launch_{kind}",
                "enabled": True,
                "label": Path(target).name,
                "target": target,
                "process": "",
                "arguments": "",
                "restore": False,
            }
        )
        self.engine.save()
        self.refresh_action_trees()

    def _selected_action(self, kind: str) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        tree = self.close_tree if kind == "close" else self.launch_tree
        selected = tree.selection()
        if not selected:
            return None
        collection = (
            self.selected_mode().setdefault("close_apps", [])
            if kind == "close"
            else self.selected_mode().setdefault("launches", [])
        )
        action = next((item for item in collection if str(item.get("id")) == selected[0]), None)
        return (collection, action) if action else None

    def toggle_action(self, kind: str) -> None:
        selected = self._selected_action(kind)
        if not selected:
            return
        _collection, action = selected
        action["enabled"] = not action.get("enabled", True)
        self.engine.save()
        self.refresh_action_trees()

    def edit_action(self, kind: str) -> None:
        selected = self._selected_action(kind)
        if not selected:
            return
        _collection, action = selected
        if action.get("type") == "close_safe_apps":
            messagebox.showinfo(
                "Dynamic action",
                "This action is generated from safe visible applications at preview time. "
                "It can be toggled or removed, but does not have a fixed process name.",
                parent=self.root,
            )
            return
        label = simpledialog.askstring(
            "Edit action",
            "Display name:",
            initialvalue=str(action.get("label") or ""),
            parent=self.root,
        )
        if not label:
            return
        if kind == "close":
            target = simpledialog.askstring(
                "Edit action",
                "Process name:",
                initialvalue=str(action.get("target") or ""),
                parent=self.root,
            )
            if not target:
                return
            target = normalize_process_name(target)
            if not is_process_allowed(target):
                messagebox.showerror(
                    "Protected process",
                    "Mode Deck refuses to add that Windows-critical process.",
                    parent=self.root,
                )
                return
            action["target"] = target
        else:
            target = simpledialog.askstring(
                "Edit action",
                "Application path, file, folder, or URL:",
                initialvalue=str(action.get("target") or ""),
                parent=self.root,
            )
            if not target:
                return
            action["target"] = target.strip()
            if action.get("type") == "launch_app":
                action["process"] = Path(os.path.expandvars(target)).stem
        action["label"] = label.strip()
        self.engine.save()
        self.refresh_action_trees()

    def remove_action(self, kind: str) -> None:
        selected = self._selected_action(kind)
        if not selected:
            return
        collection, action = selected
        collection.remove(action)
        self.engine.save()
        self.refresh_action_trees()

    def create_mode(self) -> None:
        name = simpledialog.askstring("New mode", "Mode name:", parent=self.root)
        if not name:
            return
        mode = mode_template(uuid.uuid4().hex[:10], name.strip(), "cyan")
        mode["builtin"] = False
        self.engine.modes().append(mode)
        self.selected_mode_id = str(mode["id"])
        self.config["selected_mode_id"] = self.selected_mode_id
        self.engine.save()
        self.refresh_all()

    def delete_mode(self) -> None:
        mode = self.selected_mode()
        session = self.engine.pending_session()
        if session and str(session.get("mode_id")) == str(mode.get("id")):
            messagebox.showwarning(
                "Restore first",
                "Restore the active session before deleting this mode.",
                parent=self.root,
            )
            return
        if mode.get("builtin"):
            messagebox.showinfo(
                "Built-in mode",
                "Gaming, Study, and Chill cannot be deleted. You can edit them.",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "Delete mode", f"Delete {mode.get('name')}?", parent=self.root
        ):
            return
        self.engine.modes().remove(mode)
        self.selected_mode_id = str(self.engine.modes()[0]["id"])
        self.config["selected_mode_id"] = self.selected_mode_id
        self.engine.save()
        self.refresh_all()

    def move_mode(self, direction: int) -> None:
        modes = self.engine.modes()
        index = next(
            (position for position, mode in enumerate(modes) if str(mode.get("id")) == self.selected_mode_id),
            -1,
        )
        target = index + direction
        if index < 0 or target < 0 or target >= len(modes):
            return
        modes[index], modes[target] = modes[target], modes[index]
        self.engine.save()
        self.refresh_sidebar()

    def _review_suggestions(self) -> None:
        lines = []
        for mode in self.engine.modes()[:3]:
            labels = [
                str(item.get("label"))
                for item in mode.get("close_apps", []) + mode.get("launches", [])
                if item.get("enabled")
            ]
            lines.append(f"{mode.get('name')}: {', '.join(labels) if labels else 'no apps detected'}")
        messagebox.showinfo(
            "Review detected suggestions",
            "Mode Deck found these installed applications:\n\n"
            + "\n".join(lines)
            + "\n\nThese actions are enabled as starter presets. Review, toggle, or remove "
            "them in the editor before activating a mode.",
            parent=self.root,
        )
        self.config["suggestions_reviewed"] = True
        self.engine.save()
        self.refresh_all()

    def _offer_recovery(self) -> None:
        session = self.engine.pending_session()
        if not session:
            return
        messagebox.showwarning(
            "Previous state available",
            f"{session.get('mode_name', 'A mode')} has an unfinished or active session. "
            "Use Restore previous state when you are ready.",
            parent=self.root,
        )

    def preview_and_activate(self) -> None:
        self.save_mode(silent=True)
        system = self.selected_mode().get("system", {})
        if system.get("wsl_action") == "terminate" and not system.get("wsl_distro"):
            messagebox.showwarning(
                "Choose a WSL distribution",
                "Select a WSL distribution before using the terminate action.",
                parent=self.root,
            )
            return
        if self.engine.pending_session():
            messagebox.showwarning(
                "Restore first",
                "Restore the previous state before activating another mode.",
                parent=self.root,
            )
            return
        items = self.engine.preview(self.selected_mode_id)
        selected = PreviewDialog(self.root, str(self.selected_mode().get("name")), items).show()
        if selected is None:
            return
        if not selected:
            messagebox.showinfo("Nothing selected", "Select at least one preview action.", parent=self.root)
            return
        self.activate_button.configure(state="disabled", text="Activating...")
        self.root.update_idletasks()
        try:
            session = self.engine.activate(
                self.selected_mode_id,
                selected,
                approve_force_close=lambda label: messagebox.askyesno(
                    "Application did not close",
                    f"{label} did not close normally.\n\nForce-close it? Unsaved work may be lost.",
                    parent=self.root,
                ),
                approve_wsl=lambda description: messagebox.askyesno(
                    "Stop WSL",
                    f"{description}?\n\nThis can stop Docker, servers, terminals, and unsaved Linux work.",
                    parent=self.root,
                ),
            )
            failures = [item for item in session["results"] if item["status"] == "failed"]
            messagebox.showinfo(
                "Mode active",
                f"{session.get('mode_name')} is active."
                + (f"\n\n{len(failures)} action(s) failed; restore remains available." if failures else ""),
                parent=self.root,
            )
        except Exception as exc:
            messagebox.showerror("Activation failed", str(exc), parent=self.root)
        finally:
            self.activate_button.configure(state="normal", text="Preview and activate")
            self.refresh_session_state()

    def restore_session(self) -> None:
        session = self.engine.pending_session()
        if not session:
            messagebox.showinfo("Nothing to restore", "There is no active Mode Deck session.", parent=self.root)
            return
        if not messagebox.askyesno(
            "Restore previous state",
            f"Restore the state from before {session.get('mode_name', 'this mode')}?",
            parent=self.root,
        ):
            return
        self.restore_button.configure(state="disabled", text="Restoring...")
        self.root.update_idletasks()
        try:
            restored = self.engine.restore()
            failures = [
                item for item in restored.get("restore_results", []) if item.get("status") == "failed"
            ]
            messagebox.showinfo(
                "Restore complete",
                "Previous state restored."
                + (f"\n\n{len(failures)} item(s) could not be restored." if failures else ""),
                parent=self.root,
            )
        except Exception as exc:
            messagebox.showerror("Restore failed", str(exc), parent=self.root)
        finally:
            self.refresh_session_state()

    def refresh_session_state(self) -> None:
        session = self.engine.pending_session()
        if session:
            self.session_badge.configure(text=f"ACTIVE  /  {session.get('mode_name', 'Mode')}")
            self.session_badge.grid()
            self.restore_button.configure(state="normal", text="Restore previous state")
            self.activate_button.configure(state="disabled")
        else:
            self.session_badge.grid_remove()
            self.restore_button.configure(state="disabled", text="Restore previous state")
            self.activate_button.configure(state="normal")

    def change_theme(self, _event: tk.Event | None = None) -> None:
        selected = self.theme_var.get().lower()
        if selected == self.config.get("theme"):
            return
        self.config["theme"] = selected
        self.engine.save()
        apply_theme(selected)
        for child in self.root.winfo_children():
            child.destroy()
        self.mode_buttons.clear()
        self._build_styles()
        self._build_ui()
        self.refresh_all()


def ensure_dirs() -> None:
    for path in (DATA_DIR, SESSIONS_DIR, LOGS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def run_self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="mode-deck-test-") as temp_text:
        root = Path(temp_text)
        original_candidates = globals()["installed_app_candidates"]
        try:
            globals()["installed_app_candidates"] = lambda: {
                key: {
                    "label": key.title(),
                    "path": str(root / f"{key}.exe"),
                    "process": key,
                }
                for key in (
                    "chrome",
                    "edge",
                    "vscode",
                    "steam",
                    "epic",
                    "discord",
                    "spotify",
                    "opera_gx",
                    "opencode",
                )
            }
            migration_config = default_config()
            migration_config["version"] = 2
            for mode in migration_config["modes"]:
                for action in mode["close_apps"] + mode["launches"]:
                    action["enabled"] = False
            migration_config["modes"].append(
                {
                    **mode_template("custom-empty", "Custom Empty", "cyan"),
                    "builtin": False,
                }
            )
            migration_store = ConfigStore(root / "migration.json")
            migration_store.save(migration_config)
            migrated = migration_store.load()
            assert migrated["version"] == 7
            assert sum(
                1
                for mode in migrated["modes"]
                if mode["id"] in {"gaming", "study", "chill"}
                for action in mode["close_apps"] + mode["launches"]
                if action["enabled"]
            ) == 14
            migrated_gaming = next(mode for mode in migrated["modes"] if mode["id"] == "gaming")
            assert len(migrated_gaming["close_apps"]) == 1
            assert migrated_gaming["close_apps"][0]["type"] == "close_safe_apps"
            assert [item["label"] for item in migrated_gaming["launches"]] == [
                "Steam",
                "Discord Web",
            ]
            assert migrated_gaming["launches"][1]["process"] == "opera_gx"
            assert migrated_gaming["launches"][1]["arguments"] == "https://discord.com/app"
            migrated_study = next(mode for mode in migrated["modes"] if mode["id"] == "study")
            assert [item["target"] for item in migrated_study["close_apps"]] == [
                "steam",
                "epic",
            ]
            assert [item["label"] for item in migrated_study["launches"]] == [
                "WhatsApp Web",
                "YouTube",
            ]
            assert all(item["process"] == "opera_gx" for item in migrated_study["launches"])
            migrated_vibing = next(mode for mode in migrated["modes"] if mode["id"] == "chill")
            assert migrated_vibing["name"] == "Vibing"
            assert not migrated_vibing["close_apps"]
            assert [item["label"] for item in migrated_vibing["launches"]] == [
                "Opencode",
                "Vscode",
                "Spotify",
                "Codex",
                "WhatsApp Web",
                "YouTube",
                "GitHub",
            ]
            assert migrated_vibing["launches"][3]["type"] == "launch_shell"
            custom_empty = next(mode for mode in migrated["modes"] if mode["id"] == "custom-empty")
            assert not custom_empty["close_apps"] and not custom_empty["launches"]
        finally:
            globals()["installed_app_candidates"] = original_candidates

        store = ConfigStore(root / "config.json")
        engine = ModeEngine(store, root / "sessions" / "current.json", root / "logs")
        assert {mode["id"] for mode in engine.modes()} >= {"gaming", "study", "chill"}
        custom = mode_template("custom-test", "Custom Test", "green")
        custom["builtin"] = False
        custom["close_apps"] = [
            {
                "id": "close-test",
                "type": "close_app",
                "enabled": True,
                "label": "Test App",
                "target": "testapp",
                "launch_path": str(root / "testapp.exe"),
                "launch_arguments": "",
                "restore": True,
            },
            {
                "id": "protected-test",
                "type": "close_app",
                "enabled": True,
                "label": "Windows",
                "target": "winlogon",
                "launch_path": "",
                "restore": False,
            },
        ]
        custom["launches"] = [
            {
                "id": "launch-test",
                "type": "launch_url",
                "enabled": True,
                "label": "Example",
                "target": "https://example.com",
                "process": "",
                "arguments": "",
            }
        ]
        engine.modes().append(custom)
        engine.save()
        assert ConfigStore(root / "config.json").load()["modes"][-1]["name"] == "Custom Test"
        assert not is_process_allowed("winlogon.exe")
        assert not is_process_allowed("vgtray.exe")

        original_snapshot = globals()["process_snapshot"]
        original_graceful = globals()["graceful_close"]
        original_force = globals()["force_close"]
        original_wait = globals()["wait_for_process_exit"]
        original_launch = globals()["launch_target"]
        original_active_power = globals()["active_power_plan"]
        original_notification = globals()["notification_state"]
        original_set_notifications = globals()["set_notifications_muted"]
        original_restore_notification = globals()["restore_notification_state"]
        original_set_power = globals()["set_power_plan"]
        original_is_launch = globals()["is_launch_running"]
        original_stop_wsl = globals()["stop_wsl"]
        original_start_wsl = globals()["start_wsl_distro"]
        states = {
            "running": True,
            "launched": [],
            "power": [],
            "notifications": [],
            "wsl": [],
        }
        try:
            globals()["process_snapshot"] = lambda: [
                {
                    "name": "opera.exe",
                    "pid": 1,
                    "path": str(root / "opera.exe"),
                    "command": "",
                    "main_window": 100,
                    "window_title": "Opera GX",
                    "session_id": 1,
                },
                {
                    "name": "vgtray.exe",
                    "pid": 2,
                    "path": r"C:\Program Files\Riot Vanguard\vgtray.exe",
                    "command": "",
                    "main_window": 200,
                    "window_title": "Vanguard",
                    "session_id": 1,
                },
                {
                    "name": "explorer.exe",
                    "pid": 3,
                    "path": r"C:\Windows\explorer.exe",
                    "command": "",
                    "main_window": 300,
                    "window_title": "File Explorer",
                    "session_id": 1,
                },
                {
                    "name": "TextInputHost.exe",
                    "pid": 4,
                    "path": r"C:\Windows\SystemApps\TextInputHost.exe",
                    "command": "",
                    "main_window": 400,
                    "window_title": "Microsoft Text Input Application",
                    "session_id": 1,
                },
            ]
            assert [item["name"] for item in safe_user_app_processes()] == ["opera"]

            globals()["process_snapshot"] = lambda: (
                [
                    {
                        "name": "testapp.exe",
                        "pid": 42,
                        "path": str(root / "testapp.exe"),
                        "command": "",
                        "main_window": 400,
                        "window_title": "Test App",
                        "session_id": 1,
                    }
                ]
                if states["running"]
                else []
            )
            globals()["graceful_close"] = lambda _name: states.update(running=False) or True
            globals()["force_close"] = lambda _name: states.update(running=False) or True
            globals()["wait_for_process_exit"] = lambda _name, seconds=8: not states["running"]
            globals()["launch_target"] = (
                lambda target, action_type="app", arguments="": states["launched"].append(
                    (target, action_type, arguments)
                )
            )
            globals()["active_power_plan"] = lambda: "381b4222-f694-41f0-9685-ff5bb260df2e"
            globals()["notification_state"] = lambda: {"exists": True, "value": 1}
            globals()["set_notifications_muted"] = (
                lambda muted: states["notifications"].append(muted)
            )
            globals()["restore_notification_state"] = lambda _state: None
            globals()["set_power_plan"] = lambda guid: states["power"].append(guid)
            globals()["is_launch_running"] = lambda _target, process_name="": False
            globals()["stop_wsl"] = (
                lambda action, distro="": states["wsl"].append(("stop", action, distro))
            )
            globals()["start_wsl_distro"] = (
                lambda distro: states["wsl"].append(("start", distro))
            )

            preview = engine.preview("custom-test")
            assert not engine.session_path.exists()
            assert {item.id for item in preview} == {"close-test", "protected-test", "launch-test"}
            session = engine.activate(
                "custom-test",
                {"close-test", "protected-test", "launch-test"},
                approve_force_close=lambda _label: False,
                approve_wsl=lambda _description: False,
            )
            assert session["status"] == "active"
            assert engine.session_path.exists()
            assert any(item["status"] == "refused" for item in session["results"])
            restored = engine.restore()
            assert restored["status"] == "restored"
            assert not engine.session_path.exists()
            assert states["launched"]

            custom["launches"] = []
            states["running"] = True
            globals()["graceful_close"] = lambda _name: True
            denied = engine.activate(
                "custom-test",
                {"close-test"},
                approve_force_close=lambda _label: False,
                approve_wsl=lambda _description: False,
            )
            assert any(
                item["action"] == "close-test" and item["status"] == "skipped"
                for item in denied["results"]
            )
            engine.restore()

            states["running"] = True
            approved = engine.activate(
                "custom-test",
                {"close-test"},
                approve_force_close=lambda _label: True,
                approve_wsl=lambda _description: False,
            )
            assert any(
                item["action"] == "close-test" and item.get("detail") == "forced"
                for item in approved["results"]
            )
            engine.restore()

            custom["close_apps"] = [
                {
                    **close_safe_apps_action(),
                    "id": "safe-test",
                }
            ]
            states["running"] = True
            globals()["graceful_close"] = lambda _name: states.update(running=False) or True
            safe_preview = engine.preview("custom-test")
            assert [item.id for item in safe_preview if item.group == "Close"] == [
                "safe-test:testapp"
            ]
            safe_session = engine.activate(
                "custom-test",
                {"safe-test:testapp"},
                approve_force_close=lambda _label: False,
                approve_wsl=lambda _description: False,
            )
            assert any(
                item.get("target") == "testapp" and item["status"] == "done"
                for item in safe_session["results"]
            )
            engine.restore()

            custom["close_apps"] = []
            custom["system"] = {
                "power_plan_guid": "381b4222-f694-41f0-9685-ff5bb260df2e",
                "mute_notifications": True,
                "wsl_action": "terminate",
                "wsl_distro": "Ubuntu",
            }
            system_session = engine.activate(
                "custom-test",
                {"system:power", "system:notifications", "system:wsl"},
                approve_force_close=lambda _label: False,
                approve_wsl=lambda _description: True,
            )
            assert system_session["power_changed"]
            assert system_session["notifications_changed"]
            assert system_session["wsl"]["stopped"]
            engine.restore()
            assert ("stop", "terminate", "Ubuntu") in states["wsl"]
            assert ("start", "Ubuntu") in states["wsl"]

            custom["system"] = {
                "power_plan_guid": "",
                "mute_notifications": False,
                "wsl_action": "none",
                "wsl_distro": "",
            }
            custom["launches"] = [
                {
                    "id": "opera-whatsapp",
                    "type": "launch_app",
                    "enabled": True,
                    "label": "WhatsApp Web",
                    "target": str(root / "opera.exe"),
                    "process": "opera",
                    "arguments": "https://web.whatsapp.com/",
                },
                {
                    "id": "opera-youtube",
                    "type": "launch_app",
                    "enabled": True,
                    "label": "YouTube",
                    "target": str(root / "opera.exe"),
                    "process": "opera",
                    "arguments": "https://www.youtube.com/",
                },
            ]
            globals()["is_launch_running"] = lambda _target, process_name="": True
            before_launches = len(states["launched"])
            engine.activate(
                "custom-test",
                {"opera-whatsapp", "opera-youtube"},
                approve_force_close=lambda _label: False,
                approve_wsl=lambda _description: False,
            )
            assert len(states["launched"]) == before_launches + 2
            engine.restore()
        finally:
            globals()["process_snapshot"] = original_snapshot
            globals()["graceful_close"] = original_graceful
            globals()["force_close"] = original_force
            globals()["wait_for_process_exit"] = original_wait
            globals()["launch_target"] = original_launch
            globals()["active_power_plan"] = original_active_power
            globals()["notification_state"] = original_notification
            globals()["set_notifications_muted"] = original_set_notifications
            globals()["restore_notification_state"] = original_restore_notification
            globals()["set_power_plan"] = original_set_power
            globals()["is_launch_running"] = original_is_launch
            globals()["stop_wsl"] = original_stop_wsl
            globals()["start_wsl_distro"] = original_start_wsl
    print("Self-test passed: config, preview, denylist, activation, session recovery, and restore work.")
    return 0


def run_ui_smoke_test() -> int:
    with tempfile.TemporaryDirectory(prefix="mode-deck-ui-") as temp_text:
        root_path = Path(temp_text)
        for theme_name in ("dark", "light"):
            apply_theme(theme_name)
            config = default_config()
            config["theme"] = theme_name
            config["suggestions_reviewed"] = True
            store = ConfigStore(root_path / f"{theme_name}.json")
            store.save(config)
            engine = ModeEngine(
                store,
                root_path / theme_name / "sessions" / "current.json",
                root_path / theme_name / "logs",
            )
            root = tk.Tk()
            app = ModeDeckApp(root, engine=engine, offer_suggestions=False)
            root.geometry("1120x730+10000+10000")
            root.update_idletasks()
            root.update()
            assert root.winfo_width() >= 920
            assert app.mode_buttons
            assert app.activate_button.winfo_exists()
            assert app.restore_button.instate(["disabled"])
            assert app.notebook.index("end") == 5
            opposite = "light" if theme_name == "dark" else "dark"
            app.theme_var.set(opposite.title())
            app.change_theme()
            root.update_idletasks()
            root.update()
            assert engine.config["theme"] == opposite
            assert root.cget("bg") == THEMES[opposite]["bg"]
            assert ConfigStore(store.path).load()["theme"] == opposite
            root.destroy()
    print("UI smoke test passed: dark and light themes and all editor tabs render.")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--ui-smoke-test", action="store_true")
    parser.add_argument("--preview", metavar="MODE_ID", help="Print a mode preview without changing Windows.")
    args = parser.parse_args()
    ensure_dirs()
    store = ConfigStore()
    config = store.load()
    apply_theme(str(config.get("theme") or "dark"))
    if args.self_test:
        return run_self_test()
    if args.ui_smoke_test:
        return run_ui_smoke_test()
    if args.preview:
        engine = ModeEngine(store)
        for item in engine.preview(args.preview):
            print(f"[{item.group}] {item.label}: {item.detail}")
        return 0
    root = tk.Tk()
    ModeDeckApp(root, engine=ModeEngine(store))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
