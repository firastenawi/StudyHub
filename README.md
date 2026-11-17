# Study Hub

A study workspace for Windows that runs like its own little desktop: a taskbar and Start menu, and every tool
opens in its own window. I built it because I was tired of juggling a dozen apps during the semester.

Arabic and English, dark and light themes.

## What's in it

- **Desktop**: pin programs, sites, folders and files; widgets; several desktops with their own wallpaper and taskbar
- **Notes**: Markdown notebook with search, sticky notes on the desktop, quick note from anywhere (`Ctrl+Alt+N`)
- **Study planner**: timetable, tasks, subjects, focus timer
- **PDF reader**: highlights, bookmarks, two-page view, compare two files, and search across all your PDFs
- **Lab recorder**: records commands, screenshots and phone photos during a lab and turns them into a report
- **Terminal**: PowerShell, CMD, WSL and SSH in tabs, split panes, snippets
- **Files**: file explorer, tags, a Downloads organizer
- **Tools**: clipboard history, screenshots with OCR, password vault, command palette (`Ctrl+Alt+K`)
- **Phone link**: send notes, files and links between the laptop and your phone over Wi-Fi (Android app included)

## Install

Download `StudyHub-Setup-x.y.z.exe` from [Releases](../../releases) and run it. It installs for your user only
(no admin needed) into `%LOCALAPPDATA%\Programs\StudyHub` and adds Start menu shortcuts.

Your data lives in `%APPDATA%\StudyHub` and is kept when you update or uninstall.

## Run from source

Needs Windows 10/11 and Python 3.10+ (I use 3.12).

```bash
pip install -r requirements.txt
python main.py
```

`run.bat` starts it without a console window.

## Build

```bash
pip install pyinstaller
build.bat
```

That gives you `dist\StudyHub\`. To make the installer, open `installer\StudyHub.iss` in
[Inno Setup 6](https://jrsoftware.org/isinfo.php) and compile it (or run `ISCC installer\StudyHub.iss`).

`StudyHub.exe --self-test report.json` starts the built app, checks the main parts and writes a report.

## Hotkeys

All of them can be changed in Settings.

| Shortcut | Action |
|---|---|
| `Ctrl+Alt+N` | Quick note |
| `Ctrl+Alt+S` | Save the selected text from any program as a note |
| `Ctrl+Alt+L` | Pin the copied link |
| `Ctrl+Alt+H` | Show / hide Study Hub |
| `Ctrl+Alt+K` | Search and commands |
| `Ctrl+Alt+V` | Paste the clipboard onto the desktop |
| `Ctrl+Alt+C` | Clipboard history |
| `Ctrl+Alt+P` | Screenshot |
| `Ctrl+Alt+E` | File explorer |
| `Ctrl+Alt+J` | Snippets |
| `Ctrl+Alt+D` | Lock screen |

## Project layout

```
main.py            entry point (single instance, command-line actions)
app/
  application.py   the controller that ties everything together
  config.py        paths, version, default hotkeys
  core/            logic without UI: database, i18n, phone link, PDF index, vault...
  ui/              shell, apps, pages, dialogs, widgets
  plugins/         optional add-ons (infra, projects)
  assets/          xterm.js for the terminal
installer/         Inno Setup script, icons, install/uninstall scripts
```

## License

Bundled third-party code keeps its own license: xterm.js (`app/assets/xterm/LICENSE-xterm.txt`),
KaTeX and Mermaid (`app/assets/vendor/*/LICENSE`), all MIT.
