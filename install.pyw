#!/usr/bin/env python3
"""
Установщик Matronix для Windows, macOS и Linux.

Запуск (двойной клик или из терминала):
    python install.pyw                 окно установки
    python install.pyw --cli           установка в терминале
    python install.pyw --uninstall     удаление
    python install.pyw --cli --dir ПУТЬ --no-desktop --no-menu

Что делает установщик:
  1. Проверяет версию Python (нужна 3.10+).
  2. Копирует программу в папку установки.
  3. Создаёт виртуальное окружение и ставит PySide6 (движок браузера).
  4. Создаёт ярлыки: Windows — рабочий стол и меню «Пуск»;
     Linux — .desktop-файл в меню приложений и на рабочем столе;
     macOS — приложение Matronix.app в папке «Программы» пользователя.
"""

import argparse
import base64
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

APP = "Matronix"
# Вывод на русском языке должен работать и в файлах логов, и в терминалах Windows
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SRC = Path(__file__).resolve().parent
FILES = ["matronix.py", "matronix.pyw", "requirements.txt"]
MIN_PY = (3, 10)
SYSTEM = platform.system()  # "Windows", "Darwin" или "Linux"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------------------
#  Пути
# ---------------------------------------------------------------------------
def default_install_dir() -> Path:
    if SYSTEM == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs"
        return base / APP
    if SYSTEM == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP.lower()


def venv_python(install_dir: Path) -> Path:
    if SYSTEM == "Windows":
        return install_dir / "venv" / "Scripts" / "python.exe"
    return install_dir / "venv" / "bin" / "python"


def venv_gui_python(install_dir: Path) -> Path:
    """На Windows pythonw.exe запускает программу без консоли."""
    if SYSTEM == "Windows":
        return install_dir / "venv" / "Scripts" / "pythonw.exe"
    return venv_python(install_dir)


# ---------------------------------------------------------------------------
#  Вспомогательные функции
# ---------------------------------------------------------------------------
def run(cmd, **kwargs) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          creationflags=NO_WINDOW, **kwargs)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-900:]
        raise RuntimeError(f"Ошибка при выполнении команды:\n{' '.join(map(str, cmd))}\n\n{tail}")
    return proc.stdout


def check_python():
    if sys.version_info < MIN_PY:
        raise RuntimeError(
            f"Нужен Python {MIN_PY[0]}.{MIN_PY[1]} или новее, а у вас {platform.python_version()}.\n"
            "Скачайте Python с python.org и повторите установку."
        )


def check_sources():
    missing = [f for f in FILES if not (SRC / f).exists()]
    if not (SRC / "assets").is_dir():
        missing.append("assets/")
    if missing:
        raise RuntimeError("Не найдены файлы программы: " + ", ".join(missing) +
                           "\nЗапустите установщик из папки, где лежит Matronix.")


def ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def run_powershell(script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]).strip()


def xdg_desktop_dir() -> Path:
    try:
        out = run(["xdg-user-dir", "DESKTOP"]).strip()
        if out:
            return Path(out)
    except (OSError, RuntimeError):
        pass
    return Path.home() / "Desktop"


# ---------------------------------------------------------------------------
#  Установка
# ---------------------------------------------------------------------------
def copy_files(install_dir: Path, log):
    log("Копирую файлы программы…")
    install_dir.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(SRC / name, install_dir / name)
    shutil.copytree(SRC / "assets", install_dir / "assets", dirs_exist_ok=True)
    # сам установщик — чтобы потом можно было удалить программу из этой же папки
    shutil.copy2(Path(__file__).resolve(), install_dir / "install.pyw")


def log_dir() -> Path:
    """Папка журнала установки: та же, что у самого Matronix (см. matronix.pyw)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    folder = Path(base) / "Matronix" / "logs"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = Path.home()
    return folder


def create_venv(install_dir: Path, log):
    if not venv_python(install_dir).exists():
        log("Создаю окружение Python…")
        try:
            run([sys.executable, "-m", "venv", str(install_dir / "venv")])
        except RuntimeError as exc:
            hint = ("\n\nНа Linux установите пакет python3-venv "
                    "(например: sudo apt install python3-venv)." if SYSTEM == "Linux" else "")
            raise RuntimeError(str(exc) + hint) from exc
    py = str(venv_python(install_dir))
    log("Устанавливаю PySide6 — это может занять несколько минут…")
    run([py, "-m", "pip", "install", "--upgrade", "pip", "--quiet"])
    # Первая строка requirements.txt (PySide6) обязательна. Остальные (шифрование паролей)
    # необязательны: если их не удалось поставить, браузер всё равно работает без паролей.
    reqs = [line.strip() for line in (install_dir / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")]
    for index, req in enumerate(reqs):
        try:
            run([py, "-m", "pip", "install", req, "--quiet"])
        except RuntimeError as exc:
            if index == 0:
                raise
            log(f"Не установлен компонент {req}: браузер будет работать, но пароли будут отключены.")
            log(str(exc))
    log("Движок браузера установлен.")


def make_windows_shortcuts(install_dir: Path, desktop: bool, menu: bool, log):
    target = venv_gui_python(install_dir)
    pyw = install_dir / "matronix.pyw"
    icon = install_dir / "assets" / "logo.ico"
    lines = [
        "$ws = New-Object -ComObject WScript.Shell",
        "function MakeLink($path) {",
        "  $s = $ws.CreateShortcut($path)",
        f"  $s.TargetPath = {ps_quote(str(target))}",
        f"  $s.Arguments = {ps_quote(chr(34) + str(pyw) + chr(34))}",
        f"  $s.WorkingDirectory = {ps_quote(str(install_dir))}",
        f"  $s.IconLocation = {ps_quote(str(icon) + ',0')}",
        f"  $s.Description = {ps_quote('Matronix — браузер')}",
        "  $s.Save()",
        "}",
    ]
    if desktop:
        lines.append("MakeLink (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Matronix.lnk')")
        log("Создаю ярлык на рабочем столе.")
    if menu:
        lines.append("MakeLink (Join-Path ([Environment]::GetFolderPath('Programs')) 'Matronix.lnk')")
        log("Добавляю в меню «Пуск».")
    run_powershell("\n".join(lines))


def make_linux_shortcuts(install_dir: Path, desktop: bool, menu: bool, log):
    py = venv_python(install_dir)
    content = "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP}",
        "GenericName=Web Browser",
        "Comment=Минималистичный чёрный браузер",
        f'Exec="{py}" "{install_dir / "matronix.py"}" %U',
        f"Path={install_dir}",
        f"Icon={install_dir / 'assets' / 'logo.png'}",
        "Terminal=false",
        "Categories=Network;WebBrowser;",
        "",
    ])
    entry = install_dir / "matronix.desktop"
    entry.write_text(content, encoding="utf-8")
    entry.chmod(0o755)
    if menu:
        apps = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        (apps / "matronix.desktop").write_text(content, encoding="utf-8")
        log("Добавляю в меню приложений.")
        try:
            run(["update-desktop-database", str(apps)])
        except (OSError, RuntimeError):
            pass
    if desktop:
        folder = xdg_desktop_dir()
        folder.mkdir(parents=True, exist_ok=True)
        copy = folder / "matronix.desktop"
        copy.write_text(content, encoding="utf-8")
        copy.chmod(0o755)
        log("Создаю ярлык на рабочем столе.")


def make_mac_app(install_dir: Path, log):
    apps = Path.home() / "Applications"
    bundle = apps / f"{APP}.app"
    contents = bundle / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    (contents / "Resources").mkdir(parents=True, exist_ok=True)
    launcher = contents / "MacOS" / APP
    launcher.write_text(
        "#!/bin/sh\n"
        f'exec "{venv_python(install_dir)}" "{install_dir / "matronix.py"}" "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    shutil.copy2(install_dir / "assets" / "logo.icns", contents / "Resources" / "logo.icns")
    (contents / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f"  <key>CFBundleName</key><string>{APP}</string>\n"
        f"  <key>CFBundleDisplayName</key><string>{APP}</string>\n"
        f"  <key>CFBundleExecutable</key><string>{APP}</string>\n"
        "  <key>CFBundleIdentifier</key><string>com.matronix.browser</string>\n"
        "  <key>CFBundleIconFile</key><string>logo</string>\n"
        "  <key>CFBundlePackageType</key><string>APPL</string>\n"
        "  <key>CFBundleVersion</key><string>1.0</string>\n"
        "  <key>CFBundleShortVersionString</key><string>1.0</string>\n"
        "  <key>LSMinimumSystemVersion</key><string>11.0</string>\n"
        "  <key>NSHighResolutionCapable</key><true/>\n"
        "</dict></plist>\n",
        encoding="utf-8",
    )
    log(f"Создано приложение: {bundle}")


def make_shortcuts(install_dir: Path, desktop: bool, menu: bool, log):
    if SYSTEM == "Windows":
        if desktop or menu:
            make_windows_shortcuts(install_dir, desktop, menu, log)
    elif SYSTEM == "Darwin":
        make_mac_app(install_dir, log)
    else:
        make_linux_shortcuts(install_dir, desktop, menu, log)


def install(install_dir: Path, desktop: bool, menu: bool, log):
    journal = log_dir() / "install.log"

    def write(msg: str):
        try:
            with open(journal, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
        except OSError:
            pass

    def both(msg: str):
        write(msg)
        log(msg)

    write(f"Установка: Python {platform.python_version()} ({sys.executable}), {platform.platform()}, папка {install_dir}")
    try:
        check_python()
        check_sources()
        copy_files(install_dir, both)
        create_venv(install_dir, both)
        try:
            make_shortcuts(install_dir, desktop, menu, both)
        except RuntimeError as exc:
            # Файлы уже на месте: ярлыки можно добавить вручную, поэтому не прерываем установку
            both(f"Ярлыки не созданы, запускайте matronix.pyw из папки установки.\n{exc}")
    except Exception as exc:  # noqa: BLE001 — записываем всё, что пошло не так
        write("ОШИБКА:\n" + traceback.format_exc())
        raise RuntimeError(f"{exc}\n\nЖурнал установки: {journal}") from exc
    both("")
    both(f"Готово! {APP} установлен в: {install_dir}")
    both("Данные браузера (история, закладки, настройки) хранятся отдельно и не удаляются.")


# ---------------------------------------------------------------------------
#  Удаление
# ---------------------------------------------------------------------------
def remove_shortcuts(log):
    if SYSTEM == "Windows":
        try:
            folders = run_powershell(
                "[Environment]::GetFolderPath('Desktop')\n[Environment]::GetFolderPath('Programs')"
            ).splitlines()
            for folder in folders:
                link = Path(folder.strip()) / f"{APP}.lnk"
                if link.exists():
                    link.unlink()
        except (OSError, RuntimeError):
            pass
    elif SYSTEM == "Darwin":
        shutil.rmtree(Path.home() / "Applications" / f"{APP}.app", ignore_errors=True)
    else:
        data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        for path in (data / "applications" / "matronix.desktop", xdg_desktop_dir() / "matronix.desktop"):
            if path.exists():
                path.unlink()
    log("Ярлыки удалены.")


def uninstall(install_dir: Path, log):
    if not install_dir.exists():
        raise RuntimeError(f"Программа не найдена в папке: {install_dir}")
    remove_shortcuts(log)
    log("Удаляю файлы программы…")
    shutil.rmtree(install_dir, ignore_errors=True)
    log(f"{APP} удалён. Настройки и история в папке пользователя сохранены.")


# ---------------------------------------------------------------------------
#  Интерфейс: окно (tkinter) и терминал
# ---------------------------------------------------------------------------
def run_cli(args):
    install_dir = Path(args.dir) if args.dir else default_install_dir()
    if args.uninstall:
        uninstall(install_dir, print)
        return
    print(f"{APP} — установка в {install_dir}")
    install(install_dir, desktop=not args.no_desktop, menu=not args.no_menu, log=print)


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    BG, FG, MUTED, FIELD, BTN = "#000000", "#ededed", "#6f6f6f", "#0c0c0c", "#141414"

    root = tk.Tk()
    root.title(f"Установка {APP}")
    root.configure(bg=BG)
    root.resizable(False, False)

    def label(text, size=10, color=FG, weight="normal"):
        return tk.Label(root, text=text, bg=BG, fg=color, font=("Helvetica", size, weight), anchor="w")

    label(APP, 20, "#ffffff", "bold").pack(fill="x", padx=24, pady=(20, 0))
    label("Минималистичный чёрный браузер", 10, MUTED).pack(fill="x", padx=24)

    label("Папка установки", 9, MUTED).pack(fill="x", padx=24, pady=(18, 4))
    path_row = tk.Frame(root, bg=BG)
    path_row.pack(fill="x", padx=24)
    path_var = tk.StringVar(value=str(default_install_dir()))
    entry = tk.Entry(path_row, textvariable=path_var, bg=FIELD, fg=FG, insertbackground=FG,
                     relief="flat", highlightthickness=1, highlightbackground="#1a1a1a",
                     highlightcolor="#3a3a3a", font=("Helvetica", 10))
    entry.pack(side="left", fill="x", expand=True, ipady=6)

    def browse():
        chosen = filedialog.askdirectory(initialdir=path_var.get() or str(Path.home()))
        if chosen:
            path_var.set(str(Path(chosen) / APP) if Path(chosen).name != APP else chosen)

    tk.Button(path_row, text="Обзор…", command=browse, bg=BTN, fg=FG, activebackground="#1c1c1c",
              activeforeground=FG, relief="flat", padx=12).pack(side="left", padx=(8, 0))

    desktop_var = tk.BooleanVar(value=True)
    menu_var = tk.BooleanVar(value=True)
    opts = tk.Frame(root, bg=BG)
    opts.pack(fill="x", padx=24, pady=(14, 0))
    for text, var in (("Ярлык на рабочем столе", desktop_var), ("Добавить в меню приложений", menu_var)):
        tk.Checkbutton(opts, text=text, variable=var, bg=BG, fg=FG, selectcolor=FIELD,
                       activebackground=BG, activeforeground=FG, highlightthickness=0,
                       font=("Helvetica", 10)).pack(anchor="w", pady=2)

    log_box = tk.Text(root, height=9, width=60, bg="#070707", fg="#bdbdbd", relief="flat",
                      highlightthickness=1, highlightbackground="#161616", wrap="word",
                      font=("Helvetica", 9), padx=10, pady=8)
    log_box.pack(fill="both", padx=24, pady=(16, 0))
    log_box.configure(state="disabled")

    progress = ttk.Progressbar(root, mode="indeterminate", length=200)
    progress.pack(fill="x", padx=24, pady=(10, 0))

    buttons = tk.Frame(root, bg=BG)
    buttons.pack(fill="x", padx=24, pady=(14, 20))
    install_btn = tk.Button(buttons, text="Установить", bg="#ededed", fg="#000000",
                            activebackground="#ffffff", relief="flat", padx=18, pady=6,
                            font=("Helvetica", 10, "bold"))
    uninstall_btn = tk.Button(buttons, text="Удалить", bg=BTN, fg="#ff7a7a", activebackground="#1c1c1c",
                              activeforeground="#ff7a7a", relief="flat", padx=14, pady=6)
    close_btn = tk.Button(buttons, text="Закрыть", command=root.destroy, bg=BTN, fg=FG,
                          activebackground="#1c1c1c", activeforeground=FG, relief="flat", padx=14, pady=6)
    close_btn.pack(side="right")
    uninstall_btn.pack(side="right", padx=8)
    install_btn.pack(side="right")

    def write(msg):
        log_box.configure(state="normal")
        log_box.insert("end", msg + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")

    def set_busy(busy):
        state = "disabled" if busy else "normal"
        for btn in (install_btn, uninstall_btn):
            btn.configure(state=state)
        if busy:
            progress.start(12)
        else:
            progress.stop()

    # Фоновый поток не трогает окно: он только кладёт сообщения в очередь,
    # а окно читает очередь в своём цикле (так Tk работает стабильно на всех ОС).
    messages = queue.Queue()

    def poll():
        try:
            while True:
                kind, payload = messages.get_nowait()
                if kind == "log":
                    write(payload)
                elif kind == "done":
                    set_busy(False)
                    messagebox.showinfo(APP, "Готово!")
                    return
                elif kind == "error":
                    set_busy(False)
                    messagebox.showerror(APP, payload)
                    return
        except queue.Empty:
            root.after(100, poll)

    def work(task, *task_args):
        def log(msg):
            messages.put(("log", msg))

        def runner():
            try:
                task(*task_args, log)
                messages.put(("done", None))
            except Exception as exc:  # noqa: BLE001 — показываем любую ошибку пользователю
                messages.put(("error", str(exc)))

        set_busy(True)
        threading.Thread(target=runner, daemon=True).start()
        poll()

    def do_install():
        raw = path_var.get().strip()
        if not raw:
            messagebox.showerror(APP, "Укажите папку установки.")
            return
        target = Path(raw)
        # значения из окна читаем здесь, в главном потоке (из фонового потока Tk работать не должен)
        desk, menu = desktop_var.get(), menu_var.get()
        work(lambda *a: install(target, desk, menu, a[-1]))

    def do_uninstall():
        target = Path(path_var.get().strip())
        if messagebox.askyesno(APP, f"Удалить {APP} из папки:\n{target}?"):
            work(lambda *a: uninstall(target, a[-1]))

    install_btn.configure(command=do_install)
    uninstall_btn.configure(command=do_uninstall)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=f"Установщик {APP}")
    parser.add_argument("--cli", action="store_true", help="установка в терминале без окна")
    parser.add_argument("--uninstall", action="store_true", help="удалить программу")
    parser.add_argument("--dir", help="папка установки")
    parser.add_argument("--no-desktop", action="store_true", help="не создавать ярлык на рабочем столе")
    parser.add_argument("--no-menu", action="store_true", help="не добавлять в меню приложений")
    args = parser.parse_args()

    if args.cli:
        try:
            run_cli(args)
        except RuntimeError as exc:
            print(f"\nОШИБКА: {exc}", file=sys.stderr)
            sys.exit(1)
        return
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("tkinter не найден — запускаю установку в терминале.")
        run_cli(args)
        return
    run_gui()


if __name__ == "__main__":
    main()
