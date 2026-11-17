"""
Study Hub entry point.

Parses the flags (Explorer menu, autostart, Start menu shortcuts). If Study Hub is already running, the
command goes to it over a named pipe and this process exits without importing Qt. Otherwise the app starts.

Flags:
  --minimized        start hidden in the tray (autostart)
  --show             bring the window to the front
  --quick-note       open the floating quick-note window
  --clipboard-note   save the clipboard text as a note
  --clipboard-link   pin the link that is in the clipboard
  --add-path PATH    pin a program / file / folder to the launcher
  --track-path PATH  tag a file / folder (Explorer "Tag in Study Hub")
  --note-for PATH    quick note linked to a file
  --send-phone PATH  put a file in the phone outbox (Explorer "Send to phone")
  --paste            clipboard (files / link / text) -> Study Hub desktop
  --send-to PATHS…   "Send to > Study Hub": choose what to do with many files
  --copy-to ID PATH / --move-to ID PATH / --link-to ID PATH   per-subject Explorer entries
  --quit             close the running Study Hub (used by the installer before updating)
  --pdf [PATH]       Study Hub PDF reader (Explorer "Open with", Start-menu shortcut)
  --app KEY          one Study Hub app in its own window (notes, files, timer, lab, settings…) — the
                     Start-menu shortcuts; the shell (desktop + taskbar) stays hidden
  --cleanup          remove Explorer menu / Send to / autostart (used by the uninstaller)
  --self-test FILE   check that every component works in this build, write a JSON report
"""
from __future__ import annotations

import argparse
import sys


def build_command(argv: list[str]) -> tuple[dict | None, bool]:
    parser = argparse.ArgumentParser(prog="StudyHub")
    parser.add_argument("--minimized", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--quick-note", action="store_true")
    parser.add_argument("--clipboard-note", action="store_true")
    parser.add_argument("--clipboard-link", action="store_true")
    parser.add_argument("--add-path", metavar="PATH")
    parser.add_argument("--track-path", metavar="PATH")
    parser.add_argument("--note-for", metavar="PATH")
    parser.add_argument("--send-phone", metavar="PATH")                  # Explorer "Send to phone"
    parser.add_argument("--paste", action="store_true")
    parser.add_argument("--send-to", nargs="*", metavar="PATH")          # Explorer "Send to" (many files)
    for flag in ("--copy-to", "--move-to", "--link-to"):                 # per-subject Explorer entries
        parser.add_argument(flag, nargs=2, metavar=("SUBJECT_ID", "PATH"))
    parser.add_argument("--quit", action="store_true")                   # installer: close before updating
    parser.add_argument("--pdf", nargs="?", const="", metavar="PATH")    # PDF reader (as its own app)
    parser.add_argument("--app", metavar="KEY")                          # any app in its own window
    parser.add_argument("--note", type=int, metavar="ID")                # jump list: open a note
    parser.add_argument("--do", metavar="COMMAND")                       # jump list: an app's task
    parser.add_argument("--open-path", metavar="PATH")                   # jump list: a recent folder
    args, _unknown = parser.parse_known_args(argv)

    if args.quit:
        command = {"action": "quit"}
    elif args.pdf is not None:
        command = {"action": "open_pdf", "path": args.pdf}
    elif args.app:
        command = {"action": "open_app", "key": args.app}
    elif args.note:
        command = {"action": "open_note", "id": args.note}
    elif args.do:
        command = {"action": "run_command", "key": args.do}
    elif args.open_path:
        command = {"action": "open_path", "path": args.open_path}
    elif args.add_path:
        command = {"action": "add_path", "path": args.add_path}
    elif args.track_path:
        command = {"action": "track_path", "path": args.track_path}
    elif args.note_for:
        command = {"action": "note_for", "path": args.note_for}
    elif args.send_phone:
        command = {"action": "send_phone", "path": args.send_phone}
    elif args.send_to is not None:
        command = {"action": "send_to", "paths": args.send_to}
    elif args.copy_to or args.move_to or args.link_to:
        action, (subject, path) = next((name, value) for name, value in
                                       (("copy_to", args.copy_to), ("move_to", args.move_to),
                                        ("link_to", args.link_to)) if value)
        command = {"action": action, "subject": subject, "path": path}
    elif args.paste:
        command = {"action": "paste"}
    elif args.quick_note:
        command = {"action": "quick_note"}
    elif args.clipboard_note:
        command = {"action": "clipboard_note"}
    elif args.clipboard_link:
        command = {"action": "clipboard_link"}
    elif args.show:
        command = {"action": "show"}
    else:
        command = None
    # the reader / an app opens alone, the shell stays hidden
    return command, args.minimized or args.pdf is not None or bool(args.app or args.note or args.do or args.open_path)


def main() -> int:
    argv = sys.argv[1:]
    if "--cleanup" in argv:
        # uninstaller: remove the Explorer menu, "Send to" and autostart (user data is kept)
        from app.core import shell_integration
        shell_integration.unregister_context_menu()
        shell_integration.set_autostart(False)
        shell_integration.register_pdf_handler(False)
        return 0
    if "--self-test" in argv:
        from app.selftest import run
        return run(argv[argv.index("--self-test") + 1] if len(argv) > argv.index("--self-test") + 1 else "")

    command, minimized = build_command(argv)

    # started from inside another app's package (its AppData is redirected): run as a normal program instead,
    # otherwise Study Hub would read and write a second, hidden copy of the data (app/core/sandbox.py)
    from app.core.sandbox import redirected_appdata, relaunch_outside
    if redirected_appdata() and relaunch_outside(argv):
        return 0

    from app.core.ipc import acquire_instance_lock, forward_to_running_instance, show_already_running_message
    if not acquire_instance_lock():
        # another Study Hub is running (or still starting): hand it the command, never open the
        # database a second time
        if command and command["action"] == "quit":
            forward_to_running_instance(command, wait_seconds=5)
        elif not forward_to_running_instance(command or {"action": "show"}):
            show_already_running_message()  # it can't be reached: say so instead of hanging silently
        return 0
    if command and command["action"] == "quit":
        return 0  # nothing is running: nothing to close

    from app.core.logs import setup_logging
    setup_logging()
    from app.application import StudyHubApp
    return StudyHubApp(initial_command=command, start_minimized=minimized).run()


if __name__ == "__main__":
    sys.exit(main())
