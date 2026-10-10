"""Запуск Matronix без консольного окна (на Windows — двойной клик).

Всё, что ломается при запуске, пишется в журнал:
  Windows:       %LOCALAPPDATA%\\Matronix\\logs\\matronix.log
  Linux / macOS: ~/Matronix/logs/matronix.log
Падения Chromium/Qt (до записи Python-ошибки) попадают в native-crash.log в той же папке.
"""
import faulthandler
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    folder = Path(base) / "Matronix" / "logs"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = Path.home()
    return folder


LOG_DIR = _log_dir()
LOG_FILE = LOG_DIR / "matronix.log"


def _write(text: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except OSError:
        pass


def _show_error(text: str) -> None:
    """Без консоли ошибку не видно, поэтому на Windows показываем окно."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "Matronix", 0x10)  # MB_ICONERROR


def run() -> None:
    native = open(LOG_DIR / "native-crash.log", "a", encoding="utf-8")
    faulthandler.enable(file=native, all_threads=True)
    _write(f"запуск: Python {sys.version.split()[0]}, exe={sys.executable}, "
           f"frozen={getattr(sys, 'frozen', False)}")
    try:
        import matronix
        matronix.journal = _write
        from matronix import main
        main()
    except SystemExit:
        _write("выход")
        raise
    except BaseException:
        details = traceback.format_exc()
        _write("ОШИБКА:\n" + details)
        _show_error(f"Matronix не смог запуститься.\n\nПодробности в файле:\n{LOG_FILE}")
        raise


if __name__ == "__main__":
    run()
