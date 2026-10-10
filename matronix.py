#!/usr/bin/env python3
"""
Matronix — минималистичный чёрный браузер на PySide6 (Qt WebEngine).

Установка:  pip install -r requirements.txt
Запуск:     python matronix.py [адрес_или_запрос ...]

Горячие клавиши:
  Ctrl+T          новая вкладка
  Ctrl+W          закрыть вкладку
  Ctrl+L          фокус на адресной строке
  Ctrl+Tab        следующая вкладка (Ctrl+Shift+Tab — предыдущая)
  Ctrl+R / F5     обновить (во время загрузки — остановить)
  Alt+← / Alt+→   назад / вперёд
  Ctrl+Home       домашняя страница
  Ctrl+= / Ctrl+- / Ctrl+0   масштаб
  Ctrl+H          история
  Ctrl+J          загрузки
  Ctrl+,          настройки
  Ctrl+Q          выход
  ПКМ на вкладке  закрепить / открепить / закрыть
"""

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus

from PySide6.QtCore import (
    QByteArray, QFile, QIODevice, QObject, QRectF, QSettings, QSize, QStandardPaths,
    Qt, QTimer, QUrl, Slot,
)
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QFontDatabase, QIcon, QKeySequence,
    QPainter, QPalette, QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QPushButton, QSizePolicy, QStackedWidget, QTabBar, QToolBar, QToolButton,
    QVBoxLayout, QWidget,
)

# Шифрование паролей нужно только для раздела «Пароли». Если модуль не установлен,
# браузер всё равно запускается, а пароли отключаются с объяснением.
try:
    from cryptography.fernet import Fernet, InvalidToken
    CRYPTO_PROBLEM = ""
except ImportError as _exc:  # старые установки могли не получить новую зависимость
    Fernet = None
    InvalidToken = ValueError
    CRYPTO_PROBLEM = (f"Модуль шифрования не найден ({_exc}). Чтобы сохранять пароли, переустановите "
                      "Matronix или выполните: pip install -r requirements.txt")


APP_NAME = "Matronix"
HOME_HOST = "matronix.home"          # внутренний хост стартовой страницы

# Поисковые системы: (адрес формы, имя поля, шаблон для адресной строки)
SEARCH_ENGINES = {
    "Google":     ("https://www.google.com/search", "q", "https://www.google.com/search?q={}"),
    "DuckDuckGo": ("https://duckduckgo.com/", "q", "https://duckduckgo.com/?q={}"),
    "Bing":       ("https://www.bing.com/search", "q", "https://www.bing.com/search?q={}"),
    "Yandex":     ("https://yandex.ru/search/", "text", "https://yandex.ru/search/?text={}"),
}

# Значения по умолчанию для всех настроек
DEFAULTS = {
    "search_engine": "Google",
    "home_url": "",                                   # пусто = стартовая страница Matronix
    "text_selection": False,                          # разрешить выделение текста на сайтах
    "javascript": True,
    "images": True,
    "cookies": True,                                  # сохранять cookies между запусками
    "zoom": 100,                                      # масштаб новых страниц, %
    "download_dir": str(Path.home() / "Downloads"),
    "show_statusbar": False,                          # строка состояния внизу окна
    "statusbar_migrated": False,                      # служебный флаг: строка выключена по умолчанию
    "memory_saver": True,                             # выгружать неактивные вкладки
    "save_history": True,                             # вести историю просмотров
    "theme_bg": "#000000",                            # фон интерфейса
    "theme_text": "#e6e6e6",                          # цвет текста интерфейса
    "theme_accent": "#ededed",                        # акцентный цвет
    "ui_font": "Inter",                               # шрифт интерфейса
    "ui_font_size": 12,                               # размер шрифта интерфейса, px
    "google_restyle": True,                           # оформлять Google в цветах Matronix
    "google_clean": False,                            # скрывать рекламу и боковую панель Google
    "quick_links": "[]",                             # быстрые ссылки на стартовой странице (JSON)
    "save_passwords": True,                           # предлагать сохранять пароли
    "autofill": True,                                 # подставлять сохранённые пароли
}

DISCARD_AFTER = 10 * 60          # секунд неактивности до выгрузки вкладки
DISCARD_CHECK_MS = 60_000        # как часто проверять вкладки
MAX_HISTORY = 3000               # максимум записей в истории
PINNED_WIDTH = 44                # ширина закреплённой вкладки (только иконка)


# ---------------------------------------------------------------------------
#  Хранилища: настройки и история
# ---------------------------------------------------------------------------
def config_dir() -> Path:
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class Prefs:
    def __init__(self):
        self._s = QSettings(APP_NAME, APP_NAME)

    def get(self, key):
        default = DEFAULTS[key]
        raw = self._s.value(key, default)
        if isinstance(default, bool):
            return str(raw).lower() in ("true", "1")
        if isinstance(default, int):
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default
        return "" if raw is None else str(raw)

    def update(self, values: dict):
        for key, value in values.items():
            self._s.setValue(key, value)
        self._s.sync()


def journal(text: str) -> None:
    """Запись в журнал. matronix.pyw подставляет свою функцию, которая пишет в matronix.log."""


# Разрешения, которые сайт может запросить, и как их называем в вопросе
PERMISSION_TITLES = {
    "MediaAudioCapture": "микрофон",
    "MediaVideoCapture": "камеру",
    "MediaAudioVideoCapture": "микрофон и камеру",
    "Notifications": "уведомления",
    "Geolocation": "геолокацию",
    "DesktopVideoCapture": "демонстрацию экрана",
    "DesktopAudioVideoCapture": "звук и демонстрацию экрана",
}


class PermissionStore:
    """Решения по разрешениям сайтов (микрофон, камера, уведомления), запоминаются по адресу сайта."""

    def __init__(self):
        self.path = config_dir() / "permissions.json"
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.items = data if isinstance(data, dict) else {}

    def get(self, origin: str, feature: str):
        """True/False — решение сохранено; None — ещё не спрашивали."""
        value = self.items.get(origin, {}).get(feature)
        return value if isinstance(value, bool) else None

    def set(self, origin: str, feature: str, allowed: bool):
        self.items.setdefault(origin, {})[feature] = allowed
        self._save()

    def clear(self):
        self.items = {}
        self._save()

    def _save(self):
        try:
            self.path.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass  # без записи решение действует до закрытия браузера


class HistoryStore:
    """История просмотров в JSON-файле (новые записи — в начале списка)."""

    def __init__(self):
        self.path = config_dir() / "history.json"
        try:
            self.items = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = []
        if not isinstance(self.items, list):
            self.items = []
        for entry in self.items:
            if isinstance(entry, dict) and "id" not in entry:
                entry["id"] = uuid.uuid4().hex

    def add(self, url: str, title: str):
        if not url:
            return
        if self.items and self.items[0].get("url") == url:
            self.items[0]["title"] = title or self.items[0].get("title", "")
            self.items[0]["time"] = time.time()
        else:
            self.items.insert(0, {"id": uuid.uuid4().hex, "url": url,
                                  "title": title or "", "time": time.time()})
            del self.items[MAX_HISTORY:]
        self._save()

    def remove_id(self, entry_id: str):
        self.items = [e for e in self.items if e.get("id") != entry_id]
        self._save()

    def clear(self):
        self.items = []
        self._save()

    def _save(self):
        try:
            self.path.write_text(json.dumps(self.items, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


class PasswordStore:
    """Сохранённые пароли. Файл зашифрован (Fernet), ключ лежит в папке настроек с правами 600.
    Такой ключ защищает файл при копировании, но не от всех, кто имеет доступ к учётной записи."""

    def __init__(self):
        folder = config_dir()
        self.key_path = folder / "vault.key"
        self.path = folder / "passwords.bin"
        self.readable = True          # False, если файл не расшифровывается — тогда не перезаписываем его
        self.problem = ""             # текст причины, если пароли недоступны
        self.items = []
        self._fernet = None
        if Fernet is None:
            self.readable = False
            self.problem = CRYPTO_PROBLEM
            return
        self._fernet = Fernet(self._load_or_create_key())
        self._load()
        if not self.readable:
            self.problem = ("Файл паролей не удаётся расшифровать: ключ не совпадает или файл повреждён. "
                            "Новые пароли сейчас не сохраняются, старый файл не изменён.")

    def _load_or_create_key(self) -> bytes:
        try:
            key = self.key_path.read_bytes()
            Fernet(key)
            return key
        except (OSError, ValueError):
            pass
        key = Fernet.generate_key()
        self.key_path.write_bytes(key)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    def _load(self):
        try:
            raw = self.path.read_bytes()
        except OSError:
            return
        try:
            data = json.loads(self._fernet.decrypt(raw).decode("utf-8"))
            self.items = data if isinstance(data, list) else []
        except (InvalidToken, ValueError):
            self.readable = False

    def _save(self) -> bool:
        if not self.readable:
            return False
        token = self._fernet.encrypt(json.dumps(self.items, ensure_ascii=False).encode("utf-8"))
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_bytes(token)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    def for_host(self, host: str) -> list:
        return [i for i in self.items if i.get("host") == host]

    def save(self, host: str, username: str, password: str) -> bool:
        if not self.readable:
            return False
        for item in self.items:
            if item.get("host") == host and item.get("username") == username:
                item["password"] = password
                item["time"] = time.time()
                return self._save()
        self.items.append({"id": uuid.uuid4().hex, "host": host, "username": username,
                           "password": password, "time": time.time()})
        return self._save()

    def remove(self, pid: str):
        self.items = [i for i in self.items if i.get("id") != pid]
        self._save()

    def clear(self):
        self.items = []
        self._save()


# Скрипты для страниц с паролями. Работают в изолированном мире (сайт их не видит).
PW_WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld

PW_INIT_JS = """
(function(){
  if(!window.QWebChannel || !window.qt || !qt.webChannelTransport) return;
  new QWebChannel(qt.webChannelTransport, function(channel){
    window.__matronixBridge = channel.objects.matronix;
  });
})();
"""

# Запоминает логин и пароль при отправке формы входа (submit или клик по кнопке отправки)
PW_CAPTURE_JS = """
(function(){
  if(window.__matronixPw) return;
  window.__matronixPw = true;
  function collect(form){
    if(!form || !form.querySelectorAll) return;
    var pws = form.querySelectorAll('input[type=password]'), pw = null;
    for(var i=0;i<pws.length;i++){ if(pws[i].value){ pw = pws[i]; break; } }
    if(!pw) return;
    var inputs = form.querySelectorAll('input'), user = '';
    for(var j=0;j<inputs.length;j++){
      var el = inputs[j];
      if(el === pw) break;
      var t = (el.type || 'text').toLowerCase();
      if((t === 'text' || t === 'email' || t === 'tel') && el.value) user = el.value;
    }
    if(window.__matronixBridge) window.__matronixBridge.offerCredentials(user, pw.value);
  }
  document.addEventListener('submit', function(e){ collect(e.target); }, true);
  document.addEventListener('click', function(e){
    var b = e.target && e.target.closest ? e.target.closest('button,input[type=submit],input[type=image]') : null;
    if(b && b.form && b.type !== 'button' && b.type !== 'reset') collect(b.form);
  }, true);
})();
"""

PW_FILL_BODY = """function(u, p){
  var all = document.querySelectorAll('input[type=password]'), pw = null;
  for(var i=0;i<all.length;i++){ if(!all[i].value){ pw = all[i]; break; } }
  if(!pw) return;
  var inputs = (pw.form || document).querySelectorAll('input'), user = null;
  for(var j=0;j<inputs.length;j++){
    var el = inputs[j];
    if(el === pw) break;
    var t = (el.type || 'text').toLowerCase();
    if(t === 'text' || t === 'email' || t === 'tel') user = el;
    }
  function setVal(el, v){
    var d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    d.set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  }
  if(user && !user.value && u) setVal(user, u);
  setVal(pw, p);
}"""


def password_fill_js(username: str, password: str) -> str:
    return "(" + PW_FILL_BODY + ")(" + json.dumps(username) + "," + json.dumps(password) + ");"


class PasswordBridge(QObject):
    """Мост к странице: скрипт сообщает логин и пароль. Адрес берётся из браузера, а не из страницы."""

    def __init__(self, page):
        super().__init__(page)
        self._page = page

    @Slot(str, str)
    def offerCredentials(self, user, password):  # noqa: N802 (вызывается из JS)
        self._page.password_offered(str(user)[:200], str(password)[:4096])


# ---------------------------------------------------------------------------
#  Логотип и иконки (SVG, рисуются прямо из кода — внешних файлов не нужно)
# ---------------------------------------------------------------------------
LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">
  <rect x="1" y="1" width="46" height="46" rx="12" fill="#000"/>
  <rect x="1.5" y="1.5" width="45" height="45" rx="11.5" fill="none" stroke="#fff" stroke-opacity=".16"/>
  <path d="M14 34V14l10 13 10-13v20" fill="none" stroke="#fff" stroke-width="3"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""

ICON_PATHS = {
    "back":     '<path d="M15 18l-6-6 6-6"/>',
    "forward":  '<path d="M9 18l6-6-6-6"/>',
    "reload":   '<path d="M20 11a8 8 0 1 0 2 5.3"/><path d="M20 4v7h-7"/>',
    "stop":     '<path d="M6 6l12 12M18 6L6 18"/>',
    "home":     '<path d="M4 11l8-6.5 8 6.5"/><path d="M6.5 9.8V19.5h4v-5h3v5h4V9.8"/>',
    "plus":     '<path d="M12 5v14M5 12h14"/>',
    "close":    '<path d="M7 7l10 10M17 7L7 17"/>',
    "lock":     '<rect x="5.5" y="11" width="13" height="9.5" rx="2"/><path d="M8.5 11V8a3.5 3.5 0 0 1 7 0v3"/>',
    "search":   '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>',
    "settings": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    "download": '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 19h14"/>',
    "history":  '<path d="M4 12a8 8 0 1 0 2.3-5.7L4 9"/><path d="M4 4v5h5"/><path d="M12 8v4l3 2"/>',
    "key":      '<circle cx="8" cy="15" r="4"/><path d="M11 12.5L20 4"/><path d="M16 8l3 3M14 10l2 2"/>',
}


def render_svg(markup: str, size: int, dpr: int = 2) -> QPixmap:
    """Рисует SVG в QPixmap размером size×size (с учётом HiDPI)."""
    renderer = QSvgRenderer(QByteArray(markup.encode("utf-8")))
    pm = QPixmap(QSize(size * dpr, size * dpr))
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(dpr)
    painter = QPainter(pm)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pm


def icon(name: str, color: str = "#b8b8b8", size: int = 18) -> QIcon:
    """Линейная иконка из ICON_PATHS; для выключенного состояния — серая."""
    def markup(c):
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
            f'{ICON_PATHS[name]}</svg>'
        )
    qicon = QIcon(render_svg(markup(color), size))
    qicon.addPixmap(render_svg(markup("#363636"), size), QIcon.Disabled)
    return qicon


def fmt_bytes(n: int) -> str:
    if n is None or n <= 0:
        return "—"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ГБ"


# ---------------------------------------------------------------------------
#  Стили
# ---------------------------------------------------------------------------
STYLE = """
* {
    font-family: {FONT};
    font-size: {SIZE}px;
}
QMainWindow, QWidget {
    background-color: {BG};
    color: {TEXT};
}

/* ---- Панель навигации ---- */
QToolBar {
    background: {BG};
    border: none;
    border-bottom: 1px solid #141414;
    padding: 8px 10px;
    spacing: 2px;
}
QToolButton {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 7px;
}
QToolButton:hover {
    background: #151515;
}
QToolButton:pressed {
    background: #1f1f1f;
}

/* ---- Адресная строка ---- */
QLineEdit#address {
    background: #0c0c0c;
    border: 1px solid #1a1a1a;
    border-radius: 10px;
    padding: 7px 12px 7px 4px;
    margin-left: 8px;
    min-height: 22px;
    color: {ACCENT};
    font-size: 13px;
    selection-background-color: {ACCENT};
    selection-color: {BG};
}
QLineEdit#address:focus {
    border-color: #3a3a3a;
    background: #0f0f0f;
}

/* ---- Вкладки ---- */
QTabBar {
    background: transparent;
    border: none;
}
QTabBar::tab {
    background: transparent;
    color: #6b6b6b;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 6px 8px 6px 14px;
    margin: 0 2px;
    font-size: 12px;
}
QTabBar::tab:selected {
    background: #121212;
    color: #f0f0f0;
    border-color: #1c1c1c;
}
QTabBar::tab:hover:!selected {
    background: #0d0d0d;
    color: #b5b5b5;
}
QToolButton#tabClose {
    padding: 3px;
    border-radius: 5px;
    margin-right: 2px;
}
QToolButton#tabClose:hover {
    background: #262626;
}
QToolButton#newTab {
    padding: 6px;
    margin: 0 0 4px 4px;
}

/* ---- Прогресс и статус-бар ---- */
QFrame#infobar { background: #0b0b0b; border-bottom: 1px solid #1c1c1c; }
QFrame#infobar QLabel { color: #bdbdbd; }
QProgressBar {
    background: transparent;
    border: none;
    max-height: 2px;
}
QProgressBar::chunk {
    background: {ACCENT};
}
QStatusBar {
    background: {BG};
    color: #5f5f5f;
    border-top: 1px solid #141414;
    font-size: 11px;
    min-height: 22px;
}

/* ---- Диалоги ---- */
QDialog {
    background: {BG};
}
QLabel#title {
    font-size: 18px;
    font-weight: 600;
    color: #ffffff;
}
QLabel#section {
    color: #6f6f6f;
    font-size: 11px;
    margin-top: 14px;
}
QLabel#hint {
    color: #5f5f5f;
    font-size: 11px;
}
QLabel#name {
    color: #f0f0f0;
    font-size: 13px;
}
QLineEdit, QComboBox, QSpinBox {
    background: #0c0c0c;
    border: 1px solid #1a1a1a;
    border-radius: 8px;
    padding: 6px 10px;
    min-height: 20px;
    color: {ACCENT};
    selection-background-color: {ACCENT};
    selection-color: {BG};
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border-color: #3a3a3a;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background: #0b0b0b;
    color: {ACCENT};
    border: 1px solid #1f1f1f;
    selection-background-color: #1d1d1d;
    selection-color: #ffffff;
}
QSpinBox::up-button, QSpinBox::down-button {
    width: 0px;
    border: none;
}
QListWidget {
    background: #070707;
    border: 1px solid #161616;
    border-radius: 10px;
    padding: 4px;
    color: {TEXT};
    outline: none;
}
QListWidget::item {
    padding: 6px 8px;
    border-radius: 8px;
}
QListWidget::item:selected {
    background: #1a1a1a;
    color: #ffffff;
}
QListWidget::item:hover {
    background: #111111;
}
QCheckBox {
    color: #d6d6d6;
    spacing: 10px;
    padding: 4px 0;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #333333;
    border-radius: 5px;
    background: #0c0c0c;
}
QCheckBox::indicator:checked {
    background: {ACCENT};
    border-color: {ACCENT};
}
QPushButton {
    background: #141414;
    color: {TEXT};
    border: 1px solid #232323;
    border-radius: 8px;
    padding: 7px 14px;
}
QPushButton:hover {
    background: #1c1c1c;
}
QPushButton:disabled {
    color: #3a3a3a;
    border-color: #1a1a1a;
}
QPushButton#danger {
    color: #ff7a7a;
    border-color: #3a1f1f;
}
QPushButton#primary {
    background: {ACCENT};
    color: {BG};
    border-color: {ACCENT};
    font-weight: 600;
}
QPushButton#primary:hover {
    background: #ffffff;
}

/* ---- Прочее ---- */
QToolTip {
    background: #111111;
    color: #eeeeee;
    border: 1px solid #2a2a2a;
    padding: 4px 6px;
}
QMenu {
    background: #0b0b0b;
    color: {TEXT};
    border: 1px solid #1f1f1f;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 18px;
    border-radius: 6px;
}
QMenu::item:selected {
    background: #1d1d1d;
    color: #ffffff;
}
QMenu::item:disabled {
    color: #3a3a3a;
}
QMenu::separator {
    height: 1px;
    background: #1f1f1f;
    margin: 4px 8px;
}
"""


def _hex(value: str, default: str) -> str:
    """Проверяет цвет из настроек; при ошибке берём стандартный."""
    return value if QColor(value).isValid() else default


def build_style(p) -> str:
    """Таблица стилей с цветами и шрифтом из настроек."""
    font = p.get("ui_font") or "Inter"
    return (
        STYLE.replace("{BG}", _hex(p.get("theme_bg"), "#000000"))
        .replace("{TEXT}", _hex(p.get("theme_text"), "#e6e6e6"))
        .replace("{ACCENT}", _hex(p.get("theme_accent"), "#ededed"))
        .replace("{FONT}", f'"{font}", "Segoe UI", "Ubuntu", "Helvetica Neue", Arial, sans-serif')
        .replace("{SIZE}", str(p.get("ui_font_size")))
    )


# ---------------------------------------------------------------------------
#  Стартовая страница (поисковая форма зависит от выбранной поисковой системы)
# ---------------------------------------------------------------------------
START_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Matronix</title>
<style>
  * { box-sizing: border-box; -webkit-user-select: none; user-select: none; }
  input { -webkit-user-select: text; user-select: text; }
  html, body { margin: 0; height: 100%; background: __BG__; color: __TEXT__;
               font-family: 'Inter', 'Segoe UI', Ubuntu, Arial, sans-serif; }
  body { display: flex; flex-direction: column; align-items: center;
         justify-content: center; gap: 26px; }
  .logo svg { width: 84px; height: 84px; display: block; }
  .word { font-size: 12px; font-weight: 500; letter-spacing: .55em; margin-right: -.55em;
          color: #7a7a7a; text-transform: uppercase; }
  form { position: relative; width: min(580px, 88vw); }
  form svg { position: absolute; left: 18px; top: 50%; transform: translateY(-50%);
             width: 18px; height: 18px; stroke: #5c5c5c; fill: none;
             stroke-width: 1.8; stroke-linecap: round; }
  input[type=text] { width: 100%; background: #0b0b0b; border: 1px solid #1c1c1c;
             border-radius: 14px; padding: 14px 18px 14px 48px; color: #f2f2f2;
             font-size: 15px; outline: none; transition: border-color .2s, background .2s; }
  input[type=text]::placeholder { color: #4d4d4d; }
  input[type=text]:focus { border-color: #3a3a3a; background: #0e0e0e; }
  .quick { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px;
           width: min(580px, 88vw); margin-top: -10px; }
  .quick a { color: #c8c8c8; text-decoration: none; font-size: 13px; padding: 7px 14px;
             border: 1px solid #1c1c1c; border-radius: 999px; background: #0b0b0b;
             max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .quick a:hover { border-color: #3a3a3a; color: #ffffff; }
</style>
</head>
<body>
  <div class="logo">__LOGO__</div>
  <div class="word">Matronix</div>
  <form action="__ACTION__" method="get">
    <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/></svg>
    <input type="text" name="__FIELD__" placeholder="Поиск в __ENGINE__ или адрес сайта" autofocus autocomplete="off">
  </form>
  __QUICK__
</body>
</html>
"""

# Скрипт, который запрещает выделение текста на сайтах (поля ввода остаются рабочими)
NO_SELECT_JS = (
    "(function(){"
    "var s=document.createElement('style');"
    "s.textContent='*:not(input):not(textarea):not([contenteditable=\"true\"])"
    "{-webkit-user-select:none!important;user-select:none!important}';"
    "(document.head||document.documentElement).appendChild(s);"
    "})();"
)
NO_SELECT_NAME = "matronix-noselect"

# Оформление страниц Google в цветах Matronix (CSS, применяется только к google.*)
GOOGLE_SCRIPT_NAME = "matronix-google"
GOOGLE_CSS = """
html, body, #gb, #hdtb, #hdtbSum, #cnt, #rcnt, #center_col, #main, #appbar, #tophf,
#searchform, #hdtbMenus, [role=main], header, .minidiv, .sfbg, #rso, #fbar, #foot, #gbw {
  background-color: {BG} !important;
}
body, #cnt, #rso, #center_col, #gb a, #hdtb a { color: {TEXT} !important; }
h3, h3 span { color: {ACCENT} !important; }
cite, .tjvcx, .VuuXrf { color: #7a7a7a !important; }
input, textarea, form, .RNNXgb, .SDkEP, .a4bIc {
  background-color: #0b0b0b !important; border-color: #1f1f1f !important; color: {TEXT} !important;
  box-shadow: none !important;
}
"""
GOOGLE_CLEAN_CSS = """
#rhs, #tads, #tadsb, #bottomads { display: none !important; }
"""


def google_style_script(p) -> str:
    """JS, который вставляет стили Google. Пустая строка, если оформление выключено."""
    css = ""
    if p.get("google_restyle"):
        css += GOOGLE_CSS
    if p.get("google_clean"):
        css += GOOGLE_CLEAN_CSS
    if not css:
        return ""
    css = (css.replace("{BG}", _hex(p.get("theme_bg"), "#000000"))
              .replace("{TEXT}", _hex(p.get("theme_text"), "#e6e6e6"))
              .replace("{ACCENT}", _hex(p.get("theme_accent"), "#ededed")))
    # adoptedStyleSheets не блокируется политикой CSP страницы, в отличие от <style>
    return ("(function(){if(!/(^|\\.)google\\.(com|ru|by|kz|co\\.[a-z]{2}|[a-z]{2})$/.test(location.hostname))return;"
            "var sh=new CSSStyleSheet();sh.replaceSync(" + json.dumps(css) + ");"
            "document.adoptedStyleSheets=[...document.adoptedStyleSheets,sh];})();")


# Заглушка вместо выгруженной вкладки
DISCARDED_HTML = (
    '<!doctype html><html><head><meta charset="utf-8">'
    '<style>html,body{margin:0;height:100%;background:#000}</style></head><body></body></html>'
)


QUICK_MAX = 12  # максимум быстрых ссылок


def load_quick_links(p) -> list:
    """Быстрые ссылки из настроек. Принимаем только адреса http и https."""
    try:
        data = json.loads(p.get("quick_links") or "[]")
    except ValueError:
        return []
    links = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and isinstance(item.get("url"), str) \
                and item["url"].startswith(("http://", "https://")):
            links.append({"name": str(item.get("name") or item["url"]), "url": item["url"]})
    return links[:QUICK_MAX]


def quick_links_html(links) -> str:
    if not links:
        return ""
    items = "".join(
        f'<a href="{html_escape(l["url"], quote=True)}" title="{html_escape(l["url"], quote=True)}">'
        f'{html_escape(l["name"])}</a>' for l in links)
    return f'<div class="quick">{items}</div>'


def build_start_page(engine: str, bg: str = "#000000", text: str = "#ededed", links=()) -> str:
    action, field, _ = SEARCH_ENGINES.get(engine, SEARCH_ENGINES["Google"])
    return (
        START_TEMPLATE.replace("__LOGO__", LOGO_SVG)
        .replace("__BG__", _hex(bg, "#000000"))
        .replace("__TEXT__", _hex(text, "#ededed"))
        .replace("__QUICK__", quick_links_html(links))
        .replace("__ACTION__", action)
        .replace("__FIELD__", field)
        .replace("__ENGINE__", engine)
    )


# ---------------------------------------------------------------------------
#  Вспомогательные функции
# ---------------------------------------------------------------------------
def normalize_input(text: str, engine: str = "Google") -> str | None:
    """Превращает ввод пользователя в URL: либо адрес, либо поисковый запрос."""
    text = text.strip()
    if not text:
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", text) or text.startswith(("about:", "file:")):
        return text
    if " " not in text and ("." in text or text.startswith("localhost")):
        scheme = "http://" if text.startswith("localhost") else "https://"
        return scheme + text
    template = SEARCH_ENGINES.get(engine, SEARCH_ENGINES["Google"])[2]
    return template.format(quote_plus(text))


def download_state(item):
    """Возвращает (текст статуса, процент, завершена ли загрузка)."""
    if not item.isFinished():
        total, received = item.totalBytes(), item.receivedBytes()
        pct = int(received * 100 / total) if total > 0 else 0
        return "Загрузка…", pct, False
    received, total = item.receivedBytes(), item.totalBytes()
    ok = received > 0 and (total <= 0 or received >= total)
    return ("Готово" if ok else "Отменено или ошибка"), (100 if ok else 0), True


# ---------------------------------------------------------------------------
#  Страница: window.open() и target=_blank открываются как новые вкладки
# ---------------------------------------------------------------------------
class MatronixPage(QWebEnginePage):
    def __init__(self, profile, browser, parent=None):
        super().__init__(profile, parent)
        self.browser = browser
        # Мост для паролей: скрипты работают в изолированном мире, сайт их не видит
        self.bridge = PasswordBridge(self)
        self.channel = QWebChannel(self)
        self.channel.registerObject("matronix", self.bridge)
        self.setWebChannel(self.channel, PW_WORLD)
        # Полноэкранный режим видео (кнопка YouTube, F на видео). По умолчанию Qt его запрещает.
        self.settings().setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        self.fullScreenRequested.connect(self._on_fullscreen)
        self.featurePermissionRequested.connect(self._on_permission)

    def _on_permission(self, origin, feature):  # noqa: N802 (имя из Qt API)
        journal("запрос разрешения: " + origin.toString() + " " + feature.name)
        self.browser.ask_permission(self, origin, feature)

    def _on_fullscreen(self, request):  # noqa: N802 (имя из Qt API)
        request.accept()
        self.browser.set_fullscreen(bool(request.toggleOn()))

    def createWindow(self, window_type):  # noqa: N802 (имя из Qt API)
        view = self.browser.add_tab("about:blank")
        return view.page()

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):  # noqa: N802 (имя из Qt API)
        # Адреса matronix.home обрабатывает браузер: страницы настроек и кнопки-действия
        if url.host() == HOME_HOST:
            if is_main_frame:
                view = self.parent()
                QTimer.singleShot(0, lambda: self.browser.route_internal(view, QUrl(url)))
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def javaScriptConfirm(self, security_origin, msg):  # noqa: N802 (имя из Qt API)
        answer = QMessageBox.question(self.browser, APP_NAME, msg,
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def password_offered(self, user: str, password: str):
        self.browser.on_password_offer(self.parent(), self.url(), user, password)


# ---------------------------------------------------------------------------
#  Полоса вкладок: закреплённые вкладки показываются только иконкой
# ---------------------------------------------------------------------------
class TabBar(QTabBar):
    TAB_MIN, TAB_MAX = 120, 220

    def tabSizeHint(self, index):  # noqa: N802 (имя из Qt API)
        base = super().tabSizeHint(index)
        if self.tabData(index):
            return QSize(PINNED_WIDTH, base.height())
        width = max(self.TAB_MIN, min(self.TAB_MAX, base.width()))
        return QSize(width, base.height())


# ---------------------------------------------------------------------------
#  Внутренние страницы браузера: настройки, история, загрузки, пароли.
#  Открываются во вкладках по адресам https://matronix.home/<раздел>.
#  Кнопки и формы ведут на https://matronix.home/action/... — их перехватывает MatronixPage.
# ---------------------------------------------------------------------------
INTERNAL_PAGES = ("settings", "history", "downloads", "passwords")
NAV_ITEMS = (("settings", "Настройки"), ("history", "История"),
             ("downloads", "Загрузки"), ("passwords", "Пароли"))
ACTION_URL = f"https://{HOME_HOST}/action"
BOOL_SETTINGS = ("show_statusbar", "text_selection", "javascript", "images", "cookies",
                 "memory_saver", "save_history", "google_restyle", "google_clean",
                 "save_passwords", "autofill")
THEME_COLORS = ("theme_bg", "theme_text", "theme_accent")
THEME_KEYS = ("theme_bg", "theme_text", "theme_accent", "ui_font", "ui_font_size")

PAGE_CSS = """
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; background: __BG__; color: __TEXT__;
  font-family: 'Inter', 'Segoe UI', Ubuntu, Arial, sans-serif; font-size: 14px; }
main { max-width: 800px; margin: 0 auto; padding: 26px 28px 60px; }
.top { display: flex; align-items: center; gap: 18px; padding-bottom: 16px; border-bottom: 1px solid #161616; }
.logo svg { width: 30px; height: 30px; display: block; }
nav { display: flex; gap: 4px; flex-wrap: wrap; }
nav a { color: #7a7a7a; text-decoration: none; padding: 7px 12px; border-radius: 8px; }
nav a:hover { color: __TEXT__; background: #0d0d0d; }
nav a.on { color: __TEXT__; background: #141414; }
h1 { font-size: 20px; font-weight: 600; margin: 26px 0 6px; }
h2 { font-size: 11px; font-weight: 600; letter-spacing: .18em; text-transform: uppercase;
  color: #6f6f6f; margin: 34px 0 10px; padding-top: 22px; border-top: 1px solid #141414; }
h2:first-of-type { border-top: none; }
.row { display: grid; grid-template-columns: 230px 1fr; align-items: center; gap: 16px; margin: 9px 0; }
.check { display: flex; align-items: center; gap: 10px; margin: 9px 0; color: #cfcfcf; cursor: pointer; }
.check input { accent-color: __ACCENT__; width: 16px; height: 16px; margin: 0; }
.lbl { color: #b5b5b5; }
input[type=text], input[type=number], select {
  width: 100%; background: #0b0b0b; border: 1px solid #1c1c1c; border-radius: 8px;
  padding: 8px 10px; color: __TEXT__; font: inherit; outline: none; }
input[type=number] { width: 120px; }
input[type=text]:focus, select:focus { border-color: #3a3a3a; }
input[type=color] { width: 64px; height: 32px; padding: 0; border: 1px solid #1c1c1c; border-radius: 6px; background: none; }
.btn { display: inline-block; background: #111; border: 1px solid #262626; color: __TEXT__;
  border-radius: 8px; padding: 8px 14px; font: inherit; cursor: pointer; text-decoration: none; white-space: nowrap; }
.btn:hover { border-color: #3a3a3a; background: #161616; }
.btn.primary { background: __ACCENT__; border-color: __ACCENT__; color: __BG__; font-weight: 600; }
.btn.primary:hover { opacity: .9; }
.btn.danger { color: #ff9a9a; border-color: #3a1c1c; }
.btn.small { padding: 5px 10px; font-size: 13px; }
.btn[disabled] { opacity: .4; pointer-events: none; }
.hint { color: #6b6b6b; font-size: 12px; margin: 6px 0; line-height: 1.5; }
.warn { color: #ff9a9a; }
.flash { border: 1px solid #262626; background: #0b0b0b; color: #d0d0d0; border-radius: 8px; padding: 10px 14px; margin-top: 18px; }
.item { display: flex; align-items: center; gap: 14px; padding: 11px 14px; margin: 6px 0;
  background: #070707; border: 1px solid #161616; border-radius: 10px; }
.item .grow { flex: 1; min-width: 0; }
.item .name { color: #e6e6e6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; }
a.name { text-decoration: none; }
a.name:hover { text-decoration: underline; }
.item .hint { margin: 3px 0 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.acts { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.mono { font-family: 'JetBrains Mono', Consolas, monospace; }
.secret { color: #bdbdbd; letter-spacing: .08em; }
.actions { display: flex; gap: 12px; margin-top: 26px; align-items: center; }
.quickform { display: grid; grid-template-columns: 1fr 1.6fr auto; gap: 8px; margin-top: 10px; }
.quickform input[type=text] { width: 100%; }
.fontlist { max-height: 260px; overflow-y: auto; background: #0b0b0b; border: 1px solid #1c1c1c;
  border-radius: 8px; padding: 4px; }
.fopt { display: flex; align-items: center; gap: 10px; padding: 6px 10px; border-radius: 6px;
  cursor: pointer; color: __TEXT__; font-size: 16px; }
.fopt:hover { background: #141414; }
.fopt:has(input:checked) { background: #1a1a1a; }
.fopt input { accent-color: __ACCENT__; margin: 0; }
.toolbar { margin: 12px 0 4px; }
.bar { height: 3px; background: #161616; border-radius: 2px; margin-top: 8px; overflow: hidden; }
.bar span { display: block; height: 100%; background: __ACCENT__; }
::-webkit-scrollbar { width: 10px; }
::-webkit-scrollbar-track { background: __BG__; }
::-webkit-scrollbar-thumb { background: #262626; border-radius: 5px; border: 2px solid __BG__; }
::-webkit-scrollbar-thumb:hover { background: #3a3a3a; }
"""


def esc(value) -> str:
    return html_escape(str(value), quote=True)


def confirm_attr(message: str) -> str:
    """Атрибут onclick, который спрашивает подтверждение (ответ даёт окно браузера)."""
    return f'onclick="return confirm(&quot;{esc(message)}&quot;)"'


def h2(text: str) -> str:
    return f"<h2>{esc(text)}</h2>"


def theme_of(p) -> tuple:
    return (QColor(_hex(p.get("theme_bg"), "#000000")).name(),
            QColor(_hex(p.get("theme_text"), "#e6e6e6")).name(),
            QColor(_hex(p.get("theme_accent"), "#ededed")).name())


def page_shell(title: str, active: str, body: str, theme: tuple, flash: str = "", refresh: int = 0) -> str:
    bg, text, accent = theme
    css = PAGE_CSS.replace("__BG__", bg).replace("__TEXT__", text).replace("__ACCENT__", accent)
    nav = "".join(
        '<a href="https://%s/%s"%s>%s</a>' % (HOME_HOST, key, ' class="on"' if key == active else "", label)
        for key, label in NAV_ITEMS)
    meta = '<meta http-equiv="refresh" content="%d">' % refresh if refresh else ""
    flash_html = f'<div class="flash">{esc(flash)}</div>' if flash else ""
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">' + meta +
            f"<title>{esc(title)} — Matronix</title><style>{css}</style></head><body><main>"
            f'<div class="top"><div class="logo">{LOGO_SVG}</div><nav>{nav}</nav></div>'
            f"{flash_html}{body}</main></body></html>")


def render_settings(p, theme: tuple, flash: str = "") -> str:
    bg, text, accent = theme
    engines = "".join(
        '<option value="%s"%s>%s</option>' % (esc(n), " selected" if n == p.get("search_engine") else "", esc(n))
        for n in SEARCH_ENGINES)
    families = list(QFontDatabase.families())
    current_font = p.get("ui_font")
    if current_font and current_font not in families:
        families.insert(0, current_font)
    # Каждый шрифт показан своим начертанием: название написано этим же шрифтом
    fonts = "".join(
        '<label class="fopt"><input type="radio" name="ui_font" value="%s"%s>'
        '<span style="font-family:&quot;%s&quot;, sans-serif">%s</span></label>'
        % (esc(f), " checked" if f == current_font else "", esc(f), esc(f))
        for f in families)

    def check(key: str, label: str) -> str:
        return '<label class="check"><input type="checkbox" name="%s"%s><span>%s</span></label>' % (
            key, " checked" if p.get(key) else "", esc(label))

    def text_row(label: str, name: str, value, placeholder: str = "") -> str:
        return ('<div class="row"><span class="lbl">%s</span><input type="text" name="%s" value="%s" '
                'placeholder="%s"></div>' % (esc(label), name, esc(value), esc(placeholder)))

    def num_row(label: str, name: str, value, lo: int, hi: int, step: int = 1) -> str:
        return ('<div class="row"><span class="lbl">%s</span><input type="number" name="%s" value="%s" '
                'min="%d" max="%d" step="%d"></div>' % (esc(label), name, esc(value), lo, hi, step))

    def color_row(label: str, name: str, value: str) -> str:
        return ('<div class="row"><span class="lbl">%s</span><input type="color" name="%s" value="%s"></div>'
                % (esc(label), name, esc(value)))

    links = load_quick_links(p)
    full = len(links) >= QUICK_MAX
    link_rows = "".join(
        '<div class="item"><div class="grow"><span class="name">%s</span><div class="hint mono">%s</div></div>'
        '<div class="acts"><a class="btn small danger" href="%s/quick-del?i=%d">Удалить</a></div></div>'
        % (esc(l["name"]), esc(l["url"]), ACTION_URL, n) for n, l in enumerate(links))
    dis = " disabled" if full else ""

    form = (
        f'<form method="get" action="{ACTION_URL}/save">'
        + h2("Поиск и старт")
        + f'<div class="row"><span class="lbl">Поисковая система</span><select name="search_engine">{engines}</select></div>'
        + text_row("Домашняя страница", "home_url", p.get("home_url"), "Пусто — стартовая страница Matronix")
        + h2("Отображение")
        + num_row("Масштаб новых страниц, %", "zoom", p.get("zoom"), 50, 200, 10)
        + check("show_statusbar", "Показывать строку состояния")
        + h2("Внешний вид")
        + color_row("Цвет текста", "theme_text", text)
        + color_row("Акцентный цвет", "theme_accent", accent)
        + f'<div class="row"><span class="lbl">Шрифт интерфейса</span><div class="fontlist">{fonts}</div></div>'
        + num_row("Размер шрифта, px", "ui_font_size", p.get("ui_font_size"), 10, 18)
        + check("google_restyle", "Оформлять страницы Google в цветах Matronix")
        + check("google_clean", "Скрывать рекламу и боковую панель на Google")
        + f'<div class="row"><span class="lbl"></span><div><a class="btn" href="{ACTION_URL}/reset-theme">'
          "Вернуть стандартные цвета и шрифт</a></div></div>"
        + h2("Сайты")
        + check("text_selection", "Разрешить выделение текста на сайтах")
        + check("javascript", "Выполнять JavaScript")
        + check("images", "Загружать изображения")
        + check("cookies", "Сохранять cookies между запусками")
        + check("save_passwords", "Предлагать сохранять пароли")
        + f'<div class="row"><span class="lbl">Разрешения сайтов</span><div><a class="btn" href="{ACTION_URL}/perm-clear">'
          "Забыть разрешения сайтов</a></div></div>"
        + check("autofill", "Подставлять сохранённые пароли на сайтах")
        + '<p class="hint">Выделение текста по умолчанию выключено. Изменение действует на страницы, открытые после сохранения.</p>'
        + h2("Память и история")
        + check("memory_saver", "Выгружать неактивные вкладки через 10 минут")
        + check("save_history", "Вести историю просмотров")
        + h2("Загрузки")
        + text_row("Папка для загрузок", "download_dir", p.get("download_dir"))
        + '<div class="actions"><button class="btn primary" type="submit">Сохранить</button>'
          '<span class="hint">Цвета, шрифт и поиск меняются после сохранения</span></div>'
        + "</form>"
    )
    quick = (
        h2("Быстрые ссылки на стартовой странице")
        + (link_rows or '<p class="hint">Пока нет ни одной ссылки.</p>')
        + f'<form class="quickform" method="get" action="{ACTION_URL}/quick-add">'
        + f'<input type="text" name="name" maxlength="40" placeholder="Название"{dis}>'
        + f'<input type="text" name="url" placeholder="https://example.com"{dis}>'
        + f'<button class="btn" type="submit"{dis}>Добавить</button></form>'
        + f'<p class="hint">{len(links)} из {QUICK_MAX}. Изменения сохраняются сразу.</p>'
    )
    data = (
        h2("Данные браузера")
        + f'<a class="btn danger" href="{ACTION_URL}/clear" '
          + confirm_attr("Удалить cookies, кэш и историю просмотров? Это нельзя отменить.")
          + ">Очистить cookies, кэш и историю</a>"
    )
    return page_shell("Настройки", "settings", form + quick + data, theme, flash)


def render_history(entries: list, enabled: bool, theme: tuple, flash: str = "") -> str:
    body = "<h1>История</h1>"
    if not enabled:
        body += '<p class="hint">История отключена в настройках.</p>'
    elif not entries:
        body += '<p class="hint">История пока пуста. Здесь появятся сайты, которые вы открывали.</p>'
    else:
        rows = []
        for e in entries:
            url = e.get("url", "")
            title = e.get("title") or url
            when = datetime.fromtimestamp(e.get("time", 0)).strftime("%d.%m.%Y %H:%M")
            rows.append(
                '<div class="item hist" data-q="%s"><div class="grow">'
                '<a class="name" href="%s">%s</a><div class="hint mono">%s · %s</div></div>'
                '<div class="acts"><a class="btn small danger" href="%s/history-del?id=%s">Удалить</a></div></div>'
                % (esc(f"{title} {url}".lower()), esc(url), esc(title), esc(url), when,
                   ACTION_URL, esc(e.get("id", ""))))
        body += ('<div class="toolbar"><input type="text" id="q" placeholder="Поиск по истории"></div>'
                 + "".join(rows)
                 + '<div class="actions"><a class="btn danger" href="%s/history-clear" %s>Очистить всю историю</a></div>'
                 % (ACTION_URL, confirm_attr("Удалить всю историю просмотров?"))
                 + "<script>var q=document.getElementById('q');q.oninput=function(){"
                   "var v=q.value.toLowerCase();document.querySelectorAll('.hist').forEach(function(el){"
                   "el.style.display=el.getAttribute('data-q').indexOf(v)<0?'none':'';});};</script>")
    return page_shell("История", "history", body, theme, flash)


def render_downloads(items: list, theme: tuple, flash: str = "") -> str:
    body = "<h1>Загрузки</h1>"
    if not items:
        body += '<p class="hint">Загрузок в этом сеансе пока нет.</p>'
    rows, active, has_done = [], False, False
    for it in items:
        text, pct, done = download_state(it)
        active = active or not done
        has_done = has_done or done
        token = str(id(it))
        if done:
            status = text
        else:
            status = f"{text} · {pct}% · {fmt_bytes(it.receivedBytes())} из {fmt_bytes(it.totalBytes())}"
        acts = []
        if done:
            if text == "Готово":
                acts.append('<a class="btn small" href="%s/dl-open?i=%s">Открыть</a>' % (ACTION_URL, token))
            acts.append('<a class="btn small" href="%s/dl-folder?i=%s">Папка</a>' % (ACTION_URL, token))
        else:
            acts.append('<a class="btn small danger" href="%s/dl-cancel?i=%s">Отмена</a>' % (ACTION_URL, token))
        bar = "" if done else '<div class="bar"><span style="width:%d%%"></span></div>' % pct
        rows.append('<div class="item"><div class="grow"><span class="name">%s</span>'
                    '<div class="hint">%s</div>%s</div><div class="acts">%s</div></div>'
                    % (esc(it.downloadFileName()), esc(status), bar, "".join(acts)))
    body += "".join(rows)
    if has_done:
        body += '<div class="actions"><a class="btn" href="%s/dl-clear">Очистить завершённые</a></div>' % ACTION_URL
    return page_shell("Загрузки", "downloads", body, theme, flash, refresh=2 if active else 0)


def render_passwords(items: list, readable: bool, revealed: set, theme: tuple, flash: str = "",
                     problem: str = "") -> str:
    body = "<h1>Пароли</h1>"
    body += ('<p class="hint">Пароли хранятся на этом компьютере в зашифрованном виде. Ключ лежит в папке '
             "настроек Matronix: это защищает файл при копировании, но не от всех, кто имеет доступ "
             "к вашей учётной записи.</p>")
    if not readable:
        body += '<p class="hint warn">%s</p>' % esc(problem or "Пароли сейчас недоступны.")
    elif not items:
        body += ('<p class="hint">Сохранённых паролей нет. Браузер предложит сохранить пароль, '
                 "когда вы отправите форму входа на сайте.</p>")
    else:
        rows = []
        for it in items:
            pid = it.get("id", "")
            shown = pid in revealed
            secret = esc(it.get("password", "")) if shown else "••••••••••"
            toggle = ('<a class="btn small" href="%s/pw-hide?id=%s">Скрыть</a>' if shown
                      else '<a class="btn small" href="%s/pw-show?id=%s">Показать</a>') % (ACTION_URL, pid)
            rows.append(
                '<div class="item"><div class="grow"><span class="name">%s</span>'
                '<div class="hint">%s</div></div><div class="mono secret">%s</div>'
                '<div class="acts">%s<a class="btn small danger" href="%s/pw-del?id=%s">Удалить</a></div></div>'
                % (esc(it.get("host", "")), esc(it.get("username") or "без логина"), secret,
                   toggle, ACTION_URL, pid))
        body += "".join(rows)
        body += ('<div class="actions"><a class="btn danger" href="%s/pw-clear" %s>Удалить все пароли</a></div>'
                 % (ACTION_URL, confirm_attr("Удалить все сохранённые пароли?")))
    return page_shell("Пароли", "passwords", body, theme, flash)


def _clamp_int(text, lo: int, hi: int, default: int) -> int:
    try:
        value = int(str(text).strip())
    except ValueError:
        return default
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
#  Главное окно
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.prefs = Prefs()
        if not self.prefs.get("statusbar_migrated"):
            # Однократно выключаем строку состояния у тех, у кого она была сохранена включённой
            self.prefs.update({"show_statusbar": False, "statusbar_migrated": True})
        self.history = HistoryStore()
        self.downloads = []            # активные и завершённые загрузки текущего сеанса
        self._reordering = False       # защита от рекурсии при сортировке вкладок
        self._last_index = -1
        self.passwords = PasswordStore()
        self.permissions = PermissionStore()
        self.revealed = set()          # пароли, открытые на странице «Пароли»
        self._pw_pending = None        # предложение сохранить пароль
        self._fullscreen = False       # полноэкранный режим видео
        self._fs_hidden = []           # панели, которые скрыли на время полноэкранного режима
        self._fs_state = Qt.WindowNoState

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(render_svg(LOGO_SVG, 128)))
        self.resize(1280, 800)
        self.setMinimumSize(720, 460)

        # Именованный профиль сохраняет cookies и вход между запусками.
        # defaultProfile() без имени работает в режиме «приватного» профиля и ничего не пишет на диск.
        self.profile = QWebEngineProfile(APP_NAME, self)
        self.profile.downloadRequested.connect(self.on_download)
        self._install_password_scripts()

        self._build_ui()
        self.apply_settings()

        self._discard_timer = QTimer(self)
        self._discard_timer.timeout.connect(self._discard_idle_tabs)
        self._discard_timer.start(DISCARD_CHECK_MS)

    # ----- построение интерфейса ------------------------------------------
    def _shortcut(self, keys, slot):
        """Невидимое действие только ради горячей клавиши."""
        act = QAction(self)
        act.setShortcuts([QKeySequence(k) for k in ([keys] if isinstance(keys, str) else keys)])
        act.triggered.connect(slot)
        self.addAction(act)
        return act

    def _action(self, icon_name, tip, slot, keys=None):
        act = QAction(icon(icon_name), tip, self)
        act.setToolTip(tip)
        act.setStatusTip(tip)
        if keys:
            act.setShortcuts([QKeySequence(k) for k in ([keys] if isinstance(keys, str) else keys)])
        act.triggered.connect(slot)
        self.addAction(act)
        return act

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Шапка: логотип + вкладки + кнопка «+» ---
        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 8, 10, 0)
        hl.setSpacing(6)

        logo = QLabel()
        logo.setPixmap(render_svg(LOGO_SVG, 24))
        logo.setFixedSize(24, 24)
        logo.setToolTip(APP_NAME)
        hl.addWidget(logo, 0, Qt.AlignBottom)
        hl.addSpacing(6)

        self.tabbar = TabBar()
        self.tabbar.setMovable(True)
        self.tabbar.setTabsClosable(False)
        self.tabbar.setDrawBase(False)
        self.tabbar.setExpanding(False)              # вкладки не растягиваются на всю ширину
        self.tabbar.setUsesScrollButtons(True)       # кнопки прокрутки появляются только при переполнении
        self.tabbar.setElideMode(Qt.ElideRight)
        self.tabbar.setIconSize(QSize(14, 14))
        self.tabbar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.tabbar.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabbar.customContextMenuRequested.connect(self._tab_menu)
        self.tabbar.currentChanged.connect(self._on_tab_changed)
        self.tabbar.tabMoved.connect(self._on_tab_moved)
        hl.addWidget(self.tabbar, 1, Qt.AlignBottom)

        new_btn = QToolButton()
        new_btn.setObjectName("newTab")
        new_btn.setIcon(icon("plus", "#9a9a9a", 16))
        new_btn.setToolTip("Новая вкладка (Ctrl+T)")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.clicked.connect(lambda: self.focus_new_tab())
        hl.addWidget(new_btn, 0, Qt.AlignBottom)
        root.addWidget(header)
        self.header_bar = header

        # --- Панель навигации ---
        self.toolbar = QToolBar("Навигация")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.setIconSize(QSize(18, 18))

        self.back_act = self._action("back", "Назад (Alt+←)", self._go_back, "Alt+Left")
        self.fwd_act = self._action("forward", "Вперёд (Alt+→)", self._go_forward, "Alt+Right")
        self.reload_act = self._action("reload", "Обновить (F5)", self.reload_or_stop, ["F5", "Ctrl+R"])
        self.home_act = self._action("home", "Домой (Ctrl+Home)", lambda: self.load_home(self._cur()), "Ctrl+Home")
        for act in (self.back_act, self.fwd_act, self.reload_act, self.home_act):
            self.toolbar.addAction(act)

        self.address = QLineEdit()
        self.address.setObjectName("address")
        self.address.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.address.returnPressed.connect(self.navigate)
        self.site_act = self.address.addAction(icon("search", "#6b6b6b", 15), QLineEdit.LeadingPosition)
        self.toolbar.addWidget(self.address)

        self.download_act = self._action("download", "Загрузки (Ctrl+J)", self.open_downloads, "Ctrl+J")
        self.history_act = self._action("history", "История (Ctrl+H)", self.open_history, "Ctrl+H")
        self.settings_act = self._action("settings", "Настройки (Ctrl+,)", self.open_settings, "Ctrl+,")
        self.passwords_act = self._action("key", "Пароли", self.open_passwords)
        for act in (self.download_act, self.history_act, self.passwords_act, self.settings_act):
            self.toolbar.addAction(act)
        root.addWidget(self.toolbar)
        # --- Полоса предложения сохранить пароль (появляется только при отправке формы входа) ---
        self.infobar = QFrame()
        self.infobar.setObjectName("infobar")
        ib = QHBoxLayout(self.infobar)
        ib.setContentsMargins(16, 8, 12, 8)
        self.pw_label = QLabel()
        ib.addWidget(self.pw_label, 1)
        save_pw = QPushButton("Сохранить")
        save_pw.setObjectName("primary")
        save_pw.setCursor(Qt.PointingHandCursor)
        save_pw.clicked.connect(self.save_pending_password)
        skip_pw = QPushButton("Не сейчас")
        skip_pw.setCursor(Qt.PointingHandCursor)
        skip_pw.clicked.connect(self.dismiss_pending_password)
        ib.addWidget(save_pw)
        ib.addWidget(skip_pw)
        self.infobar.hide()
        root.addWidget(self.infobar)

        # --- Индикатор загрузки и содержимое вкладок ---
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.hide()
        root.addWidget(self.progress)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.statusBar().setSizeGripEnabled(False)

        # --- Горячие клавиши без кнопок ---
        self._shortcut("Ctrl+T", lambda: self.focus_new_tab())
        self._shortcut("Ctrl+W", lambda: self.close_tab_at(self.tabbar.currentIndex()))
        self._shortcut("Ctrl+L", lambda: self.focus_address())
        self._shortcut("Ctrl+Tab", lambda: self.cycle_tab(1))
        self._shortcut("Ctrl+Shift+Tab", lambda: self.cycle_tab(-1))
        self._shortcut(["Ctrl+=", "Ctrl++"], lambda: self._zoom(0.1))
        self._shortcut("Ctrl+-", lambda: self._zoom(-0.1))
        self._shortcut("Ctrl+0", lambda: self._zoom(None))
        self._shortcut("Ctrl+Q", self.close)

    # ----- внутренние страницы: настройки, история, загрузки, пароли ----------
    def open_internal(self, name: str):
        """Открывает страницу во вкладке; если она уже открыта — переключает на неё и обновляет."""
        for i in range(self.stack.count()):
            view = self.stack.widget(i)
            url = view.url()
            if url.host() == HOME_HOST and url.path() == f"/{name}":
                self.tabbar.setCurrentIndex(i)
                self.render_internal(view, name)
                return
        self.add_tab(internal=name)

    def open_downloads(self):
        self.open_internal("downloads")

    def open_history(self):
        self.open_internal("history")

    def open_passwords(self):
        self.open_internal("passwords")

    def open_settings(self):
        self.open_internal("settings")

    def internal_html(self, name: str, flash: str = "") -> str:
        p = self.prefs
        theme = theme_of(p)
        if name == "history":
            return render_history(self.history.items, p.get("save_history"), theme, flash)
        if name == "downloads":
            return render_downloads(self.downloads, theme, flash)
        if name == "passwords":
            return render_passwords(self.passwords.items, self.passwords.readable, self.revealed, theme, flash,
                                    self.passwords.problem)
        return render_settings(p, theme, flash)

    def render_internal(self, view, name: str, flash: str = ""):
        view.setHtml(self.internal_html(name, flash), QUrl(f"https://{HOME_HOST}/{name}"))

    def route_internal(self, view, url: QUrl):
        """Переход на адрес matronix.home: показываем страницу или выполняем действие."""
        if view is None or self.stack.indexOf(view) < 0:
            return
        path = url.path()
        if path.startswith("/action/"):
            self.handle_action(view, path[len("/action/"):], url)
            return
        name = path.strip("/")
        if name in INTERNAL_PAGES:
            self.render_internal(view, name)
        else:
            self.load_home(view)

    def handle_action(self, view, action: str, url: QUrl):
        # Параметры разбираем как в HTML-формах: пробелы, «+» и %-кодирование
        params = dict(parse_qsl(url.query(), keep_blank_values=True))

        def get(key: str) -> str:
            return params.get(key, "")

        page, flash = "settings", ""
        if action == "save":
            self.save_settings_form(params)
            self.apply_settings()
            flash = "Настройки сохранены"
        elif action == "perm-clear":
            self.permissions.clear()
            flash = "Разрешения забыты: сайты спросят снова"
        elif action == "reset-theme":
            self.prefs.update({k: DEFAULTS[k] for k in THEME_KEYS})
            self.apply_settings()
            flash = "Цвета и шрифт возвращены к стандартным"
        elif action == "quick-add":
            flash = self.quick_add(get("name"), get("url"))
        elif action == "quick-del":
            flash = self.quick_delete(get("i"))
        elif action == "clear":
            self.clear_browsing_data()
            flash = "Cookies, кэш и история очищены"
        elif action.startswith("history-"):
            page = "history"
            if action == "history-del":
                self.history.remove_id(get("id"))
            elif action == "history-clear":
                self.history.clear()
        elif action.startswith("dl-"):
            page = "downloads"
            self.download_action(action, get("i"))
        elif action.startswith("pw-"):
            page = "passwords"
            pid = get("id")
            if action == "pw-show":
                self.revealed.add(pid)
            elif action == "pw-hide":
                self.revealed.discard(pid)
            elif action == "pw-del":
                self.passwords.remove(pid)
                self.revealed.discard(pid)
            elif action == "pw-clear":
                self.passwords.clear()
                self.revealed.clear()
        self.render_internal(view, page, flash)

    def save_settings_form(self, params: dict):
        def get(key: str, default: str = "") -> str:
            value = params.get(key, "")
            return value if value else default

        values = {}
        engine = get("search_engine", DEFAULTS["search_engine"])
        values["search_engine"] = engine if engine in SEARCH_ENGINES else DEFAULTS["search_engine"]
        values["home_url"] = get("home_url").strip()
        values["zoom"] = _clamp_int(get("zoom"), 50, 200, DEFAULTS["zoom"])
        values["ui_font_size"] = _clamp_int(get("ui_font_size"), 10, 18, DEFAULTS["ui_font_size"])
        values["ui_font"] = get("ui_font").strip() or DEFAULTS["ui_font"]
        for key in THEME_COLORS:
            values[key] = _hex(get(key), DEFAULTS[key])
        for key in BOOL_SETTINGS:
            values[key] = key in params
        folder = get("download_dir").strip()
        if folder:
            values["download_dir"] = folder
        self.prefs.update(values)

    def quick_add(self, name: str, url: str) -> str:
        url = (url or "").strip()
        if not url:
            return "Укажите адрес сайта"
        lower = url.lower()
        if not lower.startswith(("http://", "https://")):
            # Схемы вроде javascript: или file: отклоняем; «example.com:8080» — это адрес, не схема
            if re.match(r"^[a-z][a-z0-9+.-]*:(?!\d)", url, re.I):
                return "Допустимы только адреса http и https"
            url = "https://" + url
        target = QUrl(url)
        if not target.isValid() or not target.host():
            return "Некорректный адрес"
        links = load_quick_links(self.prefs)
        if len(links) >= QUICK_MAX:
            return f"Можно добавить не больше {QUICK_MAX} ссылок"
        label = (name or "").strip()[:40] or target.host()
        links.append({"name": label, "url": url})
        self.prefs.update({"quick_links": json.dumps(links, ensure_ascii=False)})
        self.refresh_start_pages()
        return f"Ссылка добавлена: {label}"

    def quick_delete(self, index_text: str) -> str:
        links = load_quick_links(self.prefs)
        try:
            index = int(index_text)
        except (TypeError, ValueError):
            return "Ссылка не найдена"
        if not 0 <= index < len(links):
            return "Ссылка не найдена"
        removed = links.pop(index)
        self.prefs.update({"quick_links": json.dumps(links, ensure_ascii=False)})
        self.refresh_start_pages()
        return f"Ссылка удалена: {removed['name']}"

    def refresh_start_pages(self):
        """Перерисовывает открытые стартовые страницы, чтобы изменения быстрых ссылок были видны сразу."""
        for i in range(self.stack.count()):
            view = self.stack.widget(i)
            url = view.url()
            if url.host() == HOME_HOST and url.path() in ("", "/"):
                self.load_home(view)

    def download_action(self, action: str, token: str):
        if action == "dl-clear":
            self.downloads = [i for i in self.downloads if not download_state(i)[2]]
            return
        item = next((i for i in self.downloads if str(id(i)) == token), None)
        if item is None:
            return
        if action == "dl-cancel":
            item.cancel()
        elif action == "dl-open":
            path = Path(item.downloadDirectory()) / item.downloadFileName()
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        elif action == "dl-folder":
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.downloadDirectory()))

    # ----- полноэкранный режим ----------------------------------------------
    def ask_permission(self, page, origin, feature):
        """Сайт просит микрофон, камеру и т.п.: спрашиваем один раз и запоминаем ответ."""
        name = feature.name
        title = PERMISSION_TITLES.get(name)
        if title is None:
            page.setFeaturePermission(origin, feature, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser)
            return
        key = f"{origin.scheme()}://{origin.authority()}"  # например https://сайт.ru
        allowed = self.permissions.get(key, name)
        if allowed is None:
            host = origin.host() or key
            answer = QMessageBox.question(
                self, APP_NAME, f"Сайт {host} просит доступ: {title}.\n\nРазрешить?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            allowed = answer == QMessageBox.Yes
            self.permissions.set(key, name, allowed)
        policy = (QWebEnginePage.PermissionPolicy.PermissionGrantedByUser if allowed
                  else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser)
        page.setFeaturePermission(origin, feature, policy)
        journal("решение: " + key + " " + name + " -> " + ("разрешено" if allowed else "запрещено"))

    def set_fullscreen(self, on: bool):
        """Полный экран для видео: прячем шапку, навигацию и строки, возвращаем как было."""
        if on == self._fullscreen:
            return
        self._fullscreen = on
        if on:
            self._fs_state = self.windowState() & ~Qt.WindowFullScreen
            self._fs_hidden = [w for w in (self.header_bar, self.toolbar, self.infobar, self.statusBar())
                               if not w.isHidden()]
            for w in self._fs_hidden:
                w.hide()
            self.showFullScreen()
        else:
            self.setWindowState(self._fs_state)
            for w in self._fs_hidden:
                w.show()
            self._fs_hidden = []
            self.statusBar().setVisible(bool(self.prefs.get("show_statusbar")))
            self._refresh_infobar()

    # ----- пароли -----------------------------------------------------------
    def _install_password_scripts(self):
        qwc = QFile(":/qtwebchannel/qwebchannel.js")
        if not qwc.open(QIODevice.ReadOnly):
            return
        library = bytes(qwc.readAll()).decode("utf-8")
        qwc.close()
        parts = (
            ("matronix-qwebchannel", library, QWebEngineScript.InjectionPoint.DocumentCreation),
            ("matronix-pw-init", PW_INIT_JS, QWebEngineScript.InjectionPoint.DocumentReady),
            ("matronix-pw-capture", PW_CAPTURE_JS, QWebEngineScript.InjectionPoint.DocumentReady),
        )
        for name, source, point in parts:
            script = QWebEngineScript()
            script.setName(name)
            script.setSourceCode(source)
            script.setInjectionPoint(point)
            script.setWorldId(PW_WORLD)
            script.setRunsOnSubFrames(False)
            self.profile.scripts().insert(script)

    def on_password_offer(self, view, url: QUrl, user: str, password: str):
        """Страница отправила форму входа: предлагаем сохранить пароль."""
        if not self.prefs.get("save_passwords") or not password or not self.passwords.readable:
            return
        if url.scheme() not in ("http", "https") or not url.host():
            return
        host = url.host()
        same = [i for i in self.passwords.for_host(host) if i.get("username") == user]
        if same and same[0].get("password") == password:
            return
        self._pw_pending = {"view": view, "host": host, "user": user,
                            "password": password, "update": bool(same)}
        self._refresh_infobar()

    def save_pending_password(self):
        pending, self._pw_pending = self._pw_pending, None
        self._refresh_infobar()
        if not pending:
            return
        if self.passwords.save(pending["host"], pending["user"], pending["password"]):
            self.statusBar().showMessage(f"Пароль для {pending['host']} сохранён", 4000)
        else:
            self.statusBar().showMessage("Не удалось сохранить пароль: файл паролей недоступен", 6000)

    def dismiss_pending_password(self):
        self._pw_pending = None
        self._refresh_infobar()

    def _refresh_infobar(self):
        pending = self._pw_pending
        if pending and pending["view"] is self._cur():
            verb = "Обновить пароль" if pending["update"] else "Сохранить пароль"
            who = pending["user"] or "без логина"
            self.pw_label.setText(f"{verb} для {pending['host']} ({who})?")
            self.infobar.show()
        else:
            self.infobar.hide()

    def _autofill(self, view, url: QUrl):
        """Подставляет последний сохранённый пароль для сайта, если на странице есть поле пароля."""
        if not self.prefs.get("autofill") or not self.passwords.readable:
            return
        if url.scheme() not in ("http", "https") or url.host() == HOME_HOST:
            return
        saved = self.passwords.for_host(url.host())
        if saved:
            last = max(saved, key=lambda i: i.get("time", 0))
            view.page().runJavaScript(
                password_fill_js(last.get("username", ""), last.get("password", "")), PW_WORLD)

    def apply_settings(self):
        """Применяет настройки ко всему браузеру (новым и уже открытым вкладкам)."""
        p = self.prefs
        engine = p.get("search_engine")
        js = p.get("javascript")
        images = p.get("images")
        zoom = p.get("zoom") / 100.0

        # Выделение текста на сайтах: переустанавливаем скрипт
        for script in self.profile.scripts().toList():
            if script.name() in (NO_SELECT_NAME, GOOGLE_SCRIPT_NAME):
                self.profile.scripts().remove(script)
        if not p.get("text_selection"):
            self._install_no_select_script()
        self._install_google_script()

        # Фон всегда чёрный: его больше нельзя менять в настройках
        if p.get("theme_bg") != DEFAULTS["theme_bg"]:
            p.update({"theme_bg": DEFAULTS["theme_bg"]})

        # Тема: цвета и шрифт интерфейса
        app = QApplication.instance()
        if app is not None:
            app.setPalette(make_dark_palette(_hex(p.get("theme_bg"), "#000000"),
                                             _hex(p.get("theme_text"), "#e6e6e6"),
                                             _hex(p.get("theme_accent"), "#ededed")))
            app.setStyleSheet(build_style(p))

        # Cookies
        policy = (QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
                  if p.get("cookies")
                  else QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self.profile.setPersistentCookiesPolicy(policy)

        # Уже открытые вкладки
        for i in range(self.stack.count()):
            view = self.stack.widget(i)
            s = view.page().settings()
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, js)
            s.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, images)
            view.setZoomFactor(zoom)

        self.statusBar().setVisible(p.get("show_statusbar"))
        self.address.setPlaceholderText(f"Поиск в {engine} или адрес сайта")

    def _install_google_script(self):
        source = google_style_script(self.prefs)
        if not source:
            return
        script = QWebEngineScript()
        script.setName(GOOGLE_SCRIPT_NAME)
        script.setSourceCode(source)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        self.profile.scripts().insert(script)

    def _install_no_select_script(self):
        script = QWebEngineScript()
        script.setName(NO_SELECT_NAME)
        script.setSourceCode(NO_SELECT_JS)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        self.profile.scripts().insert(script)

    def clear_browsing_data(self):
        self.profile.cookieStore().deleteAllCookies()
        self.profile.clearHttpCache()
        self.profile.clearAllVisitedLinks()
        for i in range(self.stack.count()):
            self.stack.widget(i).history().clear()
        self.history.clear()
        self._update_nav()
        self.statusBar().showMessage("Cookies, кэш и история очищены", 4000)

    # ----- вкладки --------------------------------------------------------
    def _attach_close_button(self, index: int, view):
        btn = QToolButton()
        btn.setObjectName("tabClose")
        btn.setIcon(icon("close", "#6b6b6b", 12))
        btn.setToolTip("Закрыть вкладку")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda *_, v=view: self.close_view(v))
        self.tabbar.setTabButton(index, QTabBar.RightSide, btn)

    def add_tab(self, url: str | None = None, internal: str | None = None) -> QWebEngineView:
        view = QWebEngineView()
        page = MatronixPage(self.profile, self, view)
        view.setPage(page)
        page.setBackgroundColor(QColor("#000000"))   # без белой вспышки при загрузке
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, self.prefs.get("javascript"))
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, self.prefs.get("images"))
        view.setZoomFactor(self.prefs.get("zoom") / 100.0)
        view.setProperty("last_active", time.monotonic())
        self.stack.addWidget(view)

        index = self.tabbar.addTab("Новая вкладка")
        self.tabbar.setTabData(index, False)          # False — не закреплена
        self._attach_close_button(index, view)

        view.titleChanged.connect(lambda title, v=view: self._on_title(v, title))
        view.urlChanged.connect(lambda url, v=view: self._on_url(v, url))
        view.iconChanged.connect(lambda ic, v=view: self._on_icon(v, ic))
        view.loadStarted.connect(lambda v=view: self._on_load_started(v))
        view.loadProgress.connect(lambda p, v=view: self._on_progress(v, p))
        view.loadFinished.connect(lambda ok, v=view: self._on_load_finished(v))
        page.linkHovered.connect(self.statusBar().showMessage)

        self.tabbar.setCurrentIndex(index)
        if internal is not None:
            self.render_internal(view, internal)
        elif url is None:
            self.load_home(view)
        else:
            view.load(QUrl(url))
        self._update_nav()
        return view

    def load_home(self, view):
        if view is None:
            return
        home = self.prefs.get("home_url")
        if home:
            view.load(QUrl(normalize_input(home, self.prefs.get("search_engine"))))
        else:
            view.setHtml(build_start_page(self.prefs.get("search_engine"), self.prefs.get("theme_bg"),
                                          self.prefs.get("theme_text"), load_quick_links(self.prefs)),
                         QUrl(f"https://{HOME_HOST}/"))

    def close_tab_at(self, index: int):
        if index < 0 or index >= self.stack.count():
            return
        if self.tabbar.tabData(index):
            self.statusBar().showMessage("Закреплённую вкладку нельзя закрыть — сначала открепите её", 3000)
            return
        view = self.stack.widget(index)
        if self.stack.count() == 1:              # последнюю вкладку не закрываем — открываем дом
            self.load_home(view)
            return
        self.stack.removeWidget(view)            # сначала убираем страницу…
        self.tabbar.removeTab(index)             # …потом вкладку (сигнал выставит нужную)
        view.deleteLater()

    def close_view(self, view):
        idx = self.stack.indexOf(view)
        if idx >= 0:
            self.close_tab_at(idx)

    def focus_new_tab(self):
        self.add_tab()
        self.focus_address()

    def cycle_tab(self, step: int):
        n = self.tabbar.count()
        if n > 1:
            self.tabbar.setCurrentIndex((self.tabbar.currentIndex() + step) % n)

    # ----- закрепление вкладок -------------------------------------------
    def _tab_menu(self, pos):
        index = self.tabbar.tabAt(pos)
        if index < 0:
            return
        pinned = bool(self.tabbar.tabData(index))
        menu = QMenu(self)
        pin_act = menu.addAction("Открепить вкладку" if pinned else "Закрепить вкладку")
        pin_act.triggered.connect(lambda: self.toggle_pin(index))
        menu.addSeparator()
        close_act = menu.addAction("Закрыть вкладку")
        close_act.setEnabled(not pinned)
        close_act.triggered.connect(lambda: self.close_tab_at(index))
        menu.exec(self.tabbar.mapToGlobal(pos))

    def toggle_pin(self, index: int):
        if index < 0 or index >= self.stack.count():
            return
        view = self.stack.widget(index)
        pinned = not bool(self.tabbar.tabData(index))
        self.tabbar.setTabData(index, pinned)
        if pinned:
            self.tabbar.setTabButton(index, QTabBar.RightSide, None)
            self.tabbar.setTabText(index, "")
        else:
            self._attach_close_button(index, view)
            self.tabbar.setTabText(index, view.property("title") or "Новая вкладка")
        self._sort_tabs()

    def _sort_tabs(self):
        """Закреплённые вкладки всегда стоят слева, в том же порядке."""
        if self._reordering:
            return
        self._reordering = True
        try:
            pos = 0
            i = 0
            while i < self.tabbar.count():
                if self.tabbar.tabData(i):
                    if i != pos:
                        self.tabbar.moveTab(i, pos)
                    pos += 1
                i += 1
        finally:
            self._reordering = False

    # ----- выгрузка неактивных вкладок -----------------------------------
    def _restore_discarded(self, view):
        url = view.property("discard_url") or ""
        view.setProperty("discarded", False)
        view.setProperty("discard_url", "")
        if url:
            view.load(QUrl(url))

    def _discard_idle_tabs(self):
        if not self.prefs.get("memory_saver"):
            return
        now = time.monotonic()
        current = self.stack.currentIndex()
        for i in range(self.stack.count()):
            if i == current:
                continue
            view = self.stack.widget(i)
            if view.property("discarded") or self._is_loading(view):
                continue
            if now - float(view.property("last_active") or 0) < DISCARD_AFTER:
                continue
            url = view.url()
            if url.isEmpty() or url.host() == HOME_HOST or url.scheme() not in ("http", "https", "file"):
                continue
            view.setProperty("discard_url", url.toString())
            view.setProperty("discarded", True)
            view.setHtml(DISCARDED_HTML, QUrl(f"https://{HOME_HOST}/discarded"))

    # ----- помощники ------------------------------------------------------
    def _cur(self) -> QWebEngineView | None:
        w = self.stack.currentWidget()
        return w if isinstance(w, QWebEngineView) else None

    @staticmethod
    def _is_loading(view) -> bool:
        return bool(view is not None and view.property("loading"))

    def focus_address(self):
        self.address.setFocus()
        self.address.selectAll()

    def navigate(self):
        view = self._cur()
        match = re.match(r"^matronix:/{0,2}([a-z]+)/?$", self.address.text().strip(), re.I)
        if match and view is not None and match.group(1).lower() in INTERNAL_PAGES:
            self.render_internal(view, match.group(1).lower())
            return
        url = normalize_input(self.address.text(), self.prefs.get("search_engine"))
        if view is None or url is None:
            return
        view.load(QUrl(url))
        view.setFocus()

    def _go_back(self):
        if self._cur():
            self._cur().back()

    def _go_forward(self):
        if self._cur():
            self._cur().forward()

    def reload_or_stop(self):
        view = self._cur()
        if view is None:
            return
        if self._is_loading(view):
            view.stop()
        elif view.url().host() == HOME_HOST:
            self.route_internal(view, view.url())
        else:
            view.reload()

    def _zoom(self, delta):
        view = self._cur()
        if view is None:
            return
        new = self.prefs.get("zoom") / 100.0 if delta is None else max(0.25, min(5.0, view.zoomFactor() + delta))
        view.setZoomFactor(new)

    # ----- обработчики сигналов ------------------------------------------
    def _on_tab_changed(self, index):
        # предыдущая вкладка стала неактивной — запоминаем время
        if 0 <= self._last_index < self.stack.count() and self._last_index != index:
            self.stack.widget(self._last_index).setProperty("last_active", time.monotonic())
        self._last_index = index
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        view = self._cur()
        if view is not None:
            if view.property("discarded"):
                self._restore_discarded(view)
            view.setProperty("last_active", time.monotonic())
            self.setWindowTitle(f"{view.property('title') or 'Новая вкладка'} — {APP_NAME}")
            loading = self._is_loading(view)
            self.progress.setVisible(loading)
            self.reload_act.setIcon(icon("stop") if loading else icon("reload"))
        self._refresh_infobar()
        self._update_address()
        self._update_nav()

    def _on_tab_moved(self, src, dst):
        w = self.stack.widget(src)
        self.stack.removeWidget(w)
        self.stack.insertWidget(dst, w)
        self.stack.setCurrentIndex(self.tabbar.currentIndex())
        self._sort_tabs()

    def _on_title(self, view, title):
        idx = self.stack.indexOf(view)
        if idx < 0 or view.property("discarded"):
            return
        title = title or "Новая вкладка"
        view.setProperty("title", title)
        if not self.tabbar.tabData(idx):
            self.tabbar.setTabText(idx, title)
        self.tabbar.setTabToolTip(idx, title)
        if view is self._cur():
            self.setWindowTitle(f"{title} — {APP_NAME}")

    def _on_url(self, view, url):
        idx = self.stack.indexOf(view)
        if idx >= 0:
            if url.host() == HOME_HOST:
                self.tabbar.setTabIcon(idx, QIcon(render_svg(LOGO_SVG, 16)))
                view.setProperty("icon_host", HOME_HOST)
            elif view.property("icon_host") != url.host():
                self.tabbar.setTabIcon(idx, QIcon())    # на другом сайте старая иконка не нужна
        if view is self._cur():
            self._update_address()
            self._update_nav()

    def _on_icon(self, view, ic):
        idx = self.stack.indexOf(view)
        if idx < 0 or ic.isNull() or view.url().host() == HOME_HOST:
            return
        self.tabbar.setTabIcon(idx, ic)
        view.setProperty("icon_host", view.url().host())

    def _on_load_started(self, view):
        view.setProperty("loading", True)
        if view is self._cur():
            self.progress.setValue(0)
            self.progress.show()
            self.reload_act.setIcon(icon("stop"))

    def _on_progress(self, view, value):
        if view is self._cur():
            self.progress.setValue(value)

    def _on_load_finished(self, view):
        view.setProperty("loading", False)
        url = view.url()
        self._autofill(view, url)
        if self.prefs.get("save_history") and url.scheme() in ("http", "https", "file") \
                and url.host() != HOME_HOST:
            self.history.add(url.toString(), view.title())
        if view is self._cur():
            self.progress.hide()
            self.reload_act.setIcon(icon("reload"))
            self._update_nav()
            self._update_address()

    def _update_address(self):
        view = self._cur()
        if view is None or self.address.hasFocus():
            return
        url = view.url()
        if url.isEmpty() or url.host() == HOME_HOST:
            self.address.setText("")
            self.site_act.setIcon(icon("search", "#6b6b6b", 15))
        else:
            self.address.setText(url.toString())
            if url.scheme() == "https":
                self.site_act.setIcon(icon("lock", "#8a8a8a", 14))
            else:
                self.site_act.setIcon(QIcon())
        self.address.setCursorPosition(0)

    def _update_nav(self):
        view = self._cur()
        history = view.history() if view is not None else None
        self.back_act.setEnabled(bool(history and history.canGoBack()))
        self.fwd_act.setEnabled(bool(history and history.canGoForward()))

    def on_download(self, item):
        folder = Path(self.prefs.get("download_dir") or DEFAULTS["download_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        item.setDownloadDirectory(str(folder))
        item.accept()
        self.downloads.append(item)
        self.statusBar().showMessage(f"Загрузка: {item.downloadFileName()} → {folder}", 6000)


# ---------------------------------------------------------------------------
#  Точка входа
# ---------------------------------------------------------------------------
def make_dark_palette(bg: str = "#000000", text: str = "#e6e6e6", accent: str = "#ededed") -> QPalette:
    pal = QPalette()
    colors = {
        QPalette.Window: bg,
        QPalette.WindowText: text,
        QPalette.Base: "#0a0a0a",
        QPalette.AlternateBase: "#111111",
        QPalette.ToolTipBase: "#111111",
        QPalette.ToolTipText: "#eeeeee",
        QPalette.Text: text,
        QPalette.Button: "#111111",
        QPalette.ButtonText: text,
        QPalette.BrightText: "#ffffff",
        QPalette.Highlight: accent,
        QPalette.HighlightedText: bg,
        QPalette.Link: "#ffffff",
    }
    for role, color in colors.items():
        pal.setColor(role, QColor(color))
    return pal


def main():
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    win = MainWindow()
    urls = sys.argv[1:]
    if urls:
        for u in urls:
            win.add_tab(normalize_input(u, win.prefs.get("search_engine")))
    else:
        win.add_tab()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
