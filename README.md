# Mode Deck

Mode Deck is a local Windows 10/11 desktop app that prepares the PC for an
activity and restores the previous state afterward.

Built-in modes:

- Gaming
- Study
- Chill

Custom modes can close and relaunch applications, open applications, websites,
files, and folders, select an available power plan, mute notifications, and
optionally stop WSL.

## Safety

- Every activation opens a preview first.
- Applications receive a normal close request before force-close is offered.
- Force-close and WSL shutdown require explicit confirmation.
- Windows-critical and security processes are denied.
- A restore session is written before any action is performed.
- Restoration continues when one item fails.
- Mode Deck never changes antivirus, protected services, drivers, or Windows
  security settings.

## Start

Double-click `launch_mode_deck.bat`.

Preview Gaming Mode without changing anything:

```bat
safe_preview.bat
```

## Tests

```powershell
py -3 mode_deck.py --self-test
py -3 mode_deck.py --ui-smoke-test
```

Tests use temporary folders and mocked Windows actions.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

This creates `Mode Deck.exe`. Writable config, sessions, and logs remain beside
the executable.

## Desktop shortcut

```powershell
powershell -ExecutionPolicy Bypass -File .\create_desktop_shortcut.ps1
```

## Data

- `config.json`: modes and theme
- `data/sessions/current.json`: active restore session
- `data/sessions/<id>.json`: restored session archive
- `data/logs/*.jsonl`: action history

System actions are disabled by default. On this PC, Mode Deck currently detects
the Balanced power plan and Ubuntu under WSL.
