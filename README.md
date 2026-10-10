# Matronix

Минималистичный чёрный браузер на Python (PySide6 / Qt WebEngine).

## Установка для обычного пользователя

| Система | Что делать |
|---|---|
| Windows | Запустите `MatronixSetup.exe` и следуйте мастеру. Программа ставится для текущего пользователя, без прав администратора. |
| macOS | Откройте `Matronix.dmg` и перетащите Matronix в «Программы». |
| Linux | Распакуйте `Matronix-linux.tar.gz` и запустите `Matronix/Matronix`. |

Готовые файлы создаёт `build.py` (см. ниже) или GitHub Actions.

## Запуск из исходников

```bash
pip install -r requirements.txt
python matronix.py                 # стартовая страница
python matronix.py github.com      # сразу открыть адрес
```

На Windows `matronix.pyw` запускает браузер без окна консоли (двойной клик).
Ошибки запуска и падения записываются в журнал: `%LOCALAPPDATA%\Matronix\logs\matronix.log` (Windows) или `~/Matronix/logs/matronix.log` (Linux/macOS). Падения браузера Chromium — в `native-crash.log` рядом.

## Установщик без сборки (`install.pyw`)

Ставит Matronix из исходников в виртуальное окружение, создаёт ярлыки и умеет удалять программу.

```bash
python install.pyw                          # окно установщика
python install.pyw --cli                    # то же в терминале
python install.pyw --cli --no-desktop --no-menu
python install.pyw --cli --uninstall        # удаление (настройки сохраняются)
```

Папки установки:
- Windows: `%LOCALAPPDATA%\Programs\Matronix`
- macOS: `~/Library/Application Support/Matronix`
- Linux: `~/.local/share/matronix`

## Сборка готовых программ (`build.py`)

Собирайте на той ОС, для которой нужна программа. Нужны Python (сборка проверена на 3.12) и интернет (PyInstaller, PySide6).

```bash
python build.py
```

- **Windows:** `dist\Matronix\Matronix.exe` и `dist\MatronixSetup.exe`. Установщик создаёт NSIS (`Matronix.nsi`). Установите NSIS (`choco install nsis` или nsis.sourceforge.io), иначе `.exe`-установщик пропускается.
- **macOS:** `dist/Matronix.app` и `dist/Matronix.dmg`.
- **Linux:** `dist/Matronix/Matronix` и `dist/Matronix-linux.tar.gz`.

Сборки для всех трёх систем выполняет GitHub Actions (`.github/workflows/build.yml`): запустите вручную во вкладке Actions или создайте тег `v*`. Готовые файлы появятся в разделе artifacts.

Windows 10 версии 1703 и новее, а также Windows 11 нужны для Qt 6.10+: программа использует системную библиотеку `icuuc.dll` из папки System32.

**Linux:** для окна Qt нужны системные библиотеки xcb. На большинстве рабочих столов они уже есть. Если программа пишет «no Qt platform plugin could be initialized», установите их:

```bash
sudo apt install libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-xkb1 libxkbcommon-x11-0 libxcb-image0 libxcb-render-util0 libxcb-shape0 libnss3 libnspr4 libxdamage1 libxcomposite1 libxrandr2 libxkbfile1 libxtst6 libasound2 libgbm1 libxshmfence1 libxss1 libxcursor1 libxi6
```

**Windows:** если появилась ошибка «no Qt platform plugin could be initialized», установите версию, в которой есть `platforms\qwindows.dll` (установщик с исправлением от этой сборки).

## Возможности
- Вкладки: новая, закрытие, перетаскивание, закрепление (ПКМ на вкладке), переключение Ctrl+Tab
- Иконки сайтов (favicon) на вкладках
- Адресная строка: адреса открываются напрямую, всё остальное ищется в Google (можно выбрать DuckDuckGo, Bing или Яндекс в настройках).
- Назад / вперёд / обновить / стоп / домой
- Страницы браузера во вкладках: настройки (Ctrl+,), история (Ctrl+H), загрузки (Ctrl+J), пароли. Адреса вида `matronix:settings` тоже работают в адресной строке
- Пароли: браузер предлагает сохранить логин и пароль после отправки формы входа и подставляет их при следующем открытии сайта. Пароли хранятся в `passwords.bin`, зашифрованном библиотекой `cryptography`; ключ лежит рядом в файле `vault.key` (права 600). Это защищает от копирования одного файла, но не от всех, кто имеет доступ к вашей учётной записи. Сохранение и подстановка выключаются в настройках
- Экономия памяти: неактивные вкладки после 10 минут выгружаются и открываются заново при переходе
- Полностью тёмная тема, индикатор загрузки
- Быстрые ссылки на стартовой странице: добавляются и удаляются на странице настроек сразу, по умолчанию пусто
- Внешний вид (Ctrl+,): цвета текста и акцента, шрифт и размер интерфейса. Фон всегда чёрный. В списке шрифтов каждое название написано своим шрифтом. Кнопка возврата к стандартным значениям
- Полноэкранный режим видео: кнопка на плеере работает, Esc или повторное нажатие возвращает окно
- Google в цветах Matronix: чёрный фон и акцентные цвета на страницах google.* (можно выключить, а также скрыть рекламу и боковую панель)
- Разрешения сайтов: микрофон, камера, уведомления и геолокация спрашиваются один раз для каждого сайта и запоминаются (`permissions.json`). Сбросить их можно в настройках, раздел «Сайты»
- Строка состояния внизу окна выключена по умолчанию (включается в настройках, раздел «Отображение»)
- Ничего не выделяется по умолчанию. Выделение текста на страницах включается в настройках (Ctrl+,)
- Настройки работают: JavaScript, cookies, поисковая система, папка загрузок и т.д.
- Масштабирование страниц (Ctrl+= / Ctrl+- / Ctrl+0)

Горячие клавиши перечислены в шапке `matronix.py`.

## Где что хранится
- Настройки — в системном хранилище Qt (`Matronix` в реестре Windows или в `~/.config`).
- История — `history.json`, пароли — `passwords.bin` и ключ `vault.key` в папке настроек Matronix.
- Очистить историю и cookies можно на странице настроек. Пароли удаляются на странице «Пароли».

## Файлы проекта
- `matronix.py` — сам браузер
- `matronix.pyw` — запуск без консоли (Windows)
- `install.pyw` — установщик из исходников и удаление
- `build.py` — сборка программы и установщика
- `Matronix.nsi` — сценарий установщика Windows (NSIS)
- `assets/` — логотип (SVG, PNG, ICO, ICNS)
- `.github/workflows/build.yml` — сборка для Windows, macOS и Linux
