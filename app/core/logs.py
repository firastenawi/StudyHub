"""
Log file (%APPDATA%\\StudyHub\\studyhub.log).

pythonw.exe has no console, so without this an error at startup is invisible.
Every uncaught exception is written here; the file is capped at ~1 MB (3 rotations).
"""
from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

from app.config import DATA_DIR, VERSION

LOG_PATH = DATA_DIR / "studyhub.log"
log = logging.getLogger("studyhub")


def setup_logging() -> None:
    handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.info("---- Study Hub %s starting (python %s) ----", VERSION, sys.version.split()[0])

    def excepthook(exc_type, exc, tb):
        log.error("Uncaught exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook
    threading.excepthook = lambda args: log.error("Thread exception", exc_info=(
        args.exc_type, args.exc_value, args.exc_traceback))
