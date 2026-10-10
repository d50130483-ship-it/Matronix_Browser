#!/usr/bin/env python3
"""
Сборка Matronix в готовую программу (Python на компьютере не нужен).

Собирайте на той ОС, для которой нужна программа:
  Windows: dist/Matronix/Matronix.exe  +  dist/MatronixSetup.exe (нужен NSIS: choco install nsis)
  macOS:   dist/Matronix.app           +  dist/Matronix.dmg
  Linux:   dist/Matronix/Matronix      +  dist/Matronix-linux.tar.gz

Запуск:  python build.py
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Вывод на русском языке должен работать и в файлах логов, и в терминалах Windows
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
SYSTEM = sys.platform


def run(cmd, cwd=ROOT):
    print("$", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def pyside_root() -> Path:
    """Папка установленного PySide6."""
    out = subprocess.run(
        [sys.executable, "-c", "import os, PySide6; print(os.path.dirname(PySide6.__file__))"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return Path(out)


# Плагины, которые браузеру не нужны (инструменты разработчика и т.п.)
PLUGIN_SKIP = {"designer", "qmltooling", "assetimporters", "geometryloaders",
               "sceneparsers", "scxmldatamodel", "xcbglintegrations"}


def copy_qt_plugins(root: Path, staging: Path) -> list[tuple[str, str]]:
    """Копирует все плагины Qt (окно, картинки, иконки, стили) в staging.

    Плагины лежат в plugins/ (Windows) или Qt/plugins/ (Linux, macOS).
    PyInstaller на Windows не может собрать их сам, поэтому кладём явно.
    """
    base_rel = next((rel for rel in ("plugins", "Qt/plugins") if (root / rel / "platforms").is_dir()), None)
    if base_rel is None:
        raise SystemExit(f"Не найдены плагины Qt в {root}. Переустановите PySide6.")
    pairs = []
    for kind in sorted((root / base_rel).iterdir()):
        if not kind.is_dir() or kind.name in PLUGIN_SKIP or kind.name.startswith("wayland"):
            continue
        dst = staging / base_rel / kind.name
        shutil.copytree(kind, dst)
        pairs.append((str(dst), f"PySide6/{base_rel}/{kind.name}"))
    return pairs


# Файлы Qt WebEngine (браузер Chromium внутри Qt). Без них окно браузера
# тихо закрывается. Пути относительно папки PySide6; разные для Windows и Linux.
WEBENGINE_ITEMS = [
    "QtWebEngineProcess.exe",              # Windows
    "Qt/libexec/QtWebEngineProcess",       # Linux
    "resources",                           # Windows: .pak, icudtl.dat, v8_context_snapshot.bin
    "Qt/resources",                        # Linux
    "translations/qtwebengine_locales",    # Windows: языковые файлы
    "Qt/translations/qtwebengine_locales", # Linux
]


# Языки Chromium: если нужного нет, Chromium берёт en-US. Оставляем английский и русский.
KEEP_WEBENGINE_LOCALES = {"en-US.pak", "ru.pak"}


def _webengine_ignore(directory: str, names: list[str]) -> set[str]:
    """Что не копировать: отладочные файлы, DevTools и лишние языки."""
    ignored = {n for n in names if ".debug." in n or n.startswith("qtwebengine_devtools")}
    if Path(directory).name == "qtwebengine_locales":
        ignored |= {n for n in names if n not in KEEP_WEBENGINE_LOCALES}
    return ignored


def copy_webengine_files(root: Path, staging: Path) -> list[tuple[str, str]]:
    """Копирует файлы WebEngine (без отладочных) в staging.

    Возвращает пары (что_копировать, куда_в_сборке) для --add-data.
    PyInstaller кладёт содержимое папки в указанный путь назначения.
    """
    pairs = []
    for rel in WEBENGINE_ITEMS:
        src = root / rel
        if not src.exists():
            continue
        dst = staging / rel
        if src.is_dir():
            shutil.copytree(src, dst, ignore=_webengine_ignore)
            pairs.append((str(dst), f"PySide6/{rel}"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            parent = Path(rel).parent.as_posix()
            pairs.append((str(dst), "PySide6" if parent == "." else f"PySide6/{parent}"))
    if not pairs:
        raise SystemExit(f"В PySide6 не найдены файлы WebEngine в {root}. Переустановите PySide6.")
    return pairs


def build_app():
    icon = {"win32": "assets/logo.ico", "darwin": "assets/logo.icns"}.get(SYSTEM)
    root = pyside_root()
    staging = ROOT / "build" / "qt_staging"
    if staging.exists():
        shutil.rmtree(staging)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", "Matronix",
        "--add-data", f"assets{os.pathsep}assets",
        "--collect-submodules", "PySide6",
    ]
    # Плагины Qt: без плагина окна программа не запускается
    # ("no Qt platform plugin could be initialized"), без картинок не видны иконки сайтов.
    for src, dest in copy_qt_plugins(root, staging):
        cmd += ["--add-data", f"{src}{os.pathsep}{dest}"]
    # Файлы браузера Chromium (QtWebEngineProcess, ресурсы, языки)
    for src, dest in copy_webengine_files(root, staging):
        cmd += ["--add-data", f"{src}{os.pathsep}{dest}"]
    if icon:
        cmd += ["--icon", icon]
    cmd.append("matronix.pyw")  # .pyw: в Windows без окна консоли и с журналом ошибок
    run(cmd)
    check_bundle()


def check_bundle():
    """Проверяем, что нужные файлы попали в сборку, иначе установщик будет сломан."""
    plugin = {"win32": "qwindows.dll", "darwin": "libqcocoa.dylib"}.get(SYSTEM, "libqxcb.so")
    required = {
        "плагин окна Qt": [p for p in DIST.rglob(plugin) if p.parent.name == "platforms"],
        "процесс браузера Chromium": list(DIST.rglob("QtWebEngineProcess*")),
        "ресурсы браузера (qtwebengine_resources.pak)": list(DIST.rglob("qtwebengine_resources.pak")),
        "icudtl.dat": list(DIST.rglob("icudtl.dat")),
    }
    missing = [name for name, found in required.items() if not found]
    if missing:
        raise SystemExit("В сборку не попали: " + ", ".join(missing) + ". Сборка остановлена.")
    print("Проверка сборки пройдена:", ", ".join(required))


def package_windows():
    roots = [os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("ProgramFiles", r"C:\Program Files")]
    candidates = [Path(root) / "NSIS" / "makensis.exe" for root in roots]
    found = shutil.which("makensis")
    compiler = Path(found) if found else next((p for p in candidates if p.exists()), None)
    if compiler is None:
        print("\nNSIS не найден — пропускаю создание MatronixSetup.exe.")
        print("Установите NSIS (nsis.sourceforge.io) или выполните: choco install nsis")
        return
    run([str(compiler), "Matronix.nsi"])


def package_macos():
    app = DIST / "Matronix.app"
    dmg = DIST / "Matronix.dmg"
    if dmg.exists():
        dmg.unlink()
    run(["hdiutil", "create", "-volname", "Matronix", "-srcfolder", str(app),
         "-ov", "-format", "UDZO", str(dmg)])


def package_linux():
    archive = shutil.make_archive(str(DIST / "Matronix-linux"), "gztar", root_dir=DIST, base_dir="Matronix")
    print("Создан архив:", archive)


def main():
    run([sys.executable, "-m", "pip", "install", "--quiet", "-r", "requirements.txt", "pyinstaller"])
    build_app()
    if SYSTEM == "win32":
        package_windows()
    elif SYSTEM == "darwin":
        package_macos()
    else:
        package_linux()
    print("\nГотово. Результаты в папке dist/")


if __name__ == "__main__":
    main()
