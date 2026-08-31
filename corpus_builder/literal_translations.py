"""Переводы литералов интерфейса (Б7): ключ = русский текст из кода.

Так ~90 зашитых по-русски строк диалогов получают перевод, не порождая 90
ключей: `trl("Сохранено")` для русской локали возвращает вход (тождество),
для английской — значение из EN ниже.

Не найденный здесь текст не ломает интерфейс: `trl` вернёт русский и
запишет в лог «Нет перевода литерала». Полноту покрывает тест
tests/test_review_fixes_data.py::test_every_trl_literal_has_english_*.
"""

# fmt: off
EN: dict[str, str] = {
    "<h3>CorpusBuilder</h3><p>Сборщик сырого корпуса для pretraining LLM</p><p>Версия: {0}</p><p>GitHub: <a href=\"https://github.com/draco74-glitch/corpus_builder\">github.com/draco74-glitch/corpus_builder</a></p><p>Поддерживаемые источники: HTML, PDF, GitHub, StackExchange, DOAJ, arXiv, Crossref, Wikipedia</p>":
        "<h3>CorpusBuilder</h3><p>Raw corpus builder for LLM pretraining</p><p>Version: {0}</p><p>GitHub: <a href=\"https://github.com/draco74-glitch/corpus_builder\">github.com/draco74-glitch/corpus_builder</a></p><p>Supported sources: HTML, PDF, GitHub, StackExchange, DOAJ, arXiv, Crossref, Wikipedia</p>",
    "HTML-отчёт сохранён:\n{0}":
        "HTML report saved:\n{0}",
    "YAML валиден":
        "YAML is valid",
    "YAML валиден.\nВерхнеуровневые ключи: {0}":
        "YAML is valid.\nTop-level keys: {0}",
    "Готово":
        "Done",
    "Добавьте минимум 2 файла для объединения.":
        "Add at least 2 files to merge.",
    "Дождитесь завершения текущей задачи.":
        "Wait for the current task to finish.",
    "Доступно обновление":
        "Update available",
    "Занято":
        "Busy",
    "Записей: {0}\nПапка: {1}":
        "Records: {0}\nFolder: {1}",
    "Записей: {0}\nРазмер: {1} байт\nФайл: {2}":
        "Records: {0}\nSize: {1} bytes\nFile: {2}",
    "Импортировано":
        "Imported",
    "Источники найдены":
        "Sources found",
    "Коммит: {0}\nАвтор: {1}\nСообщение: {2}\n\nПрименить обновление?\nБудут скачаны и заменены .py файлы.\nПрограмму нужно будет перезапустить.":
        "Commit: {0}\nAuthor: {1}\nMessage: {2}\n\nApply the update?\n.py files will be downloaded and replaced.\nThe app must be restarted afterwards.",
    "Конфиг пустой":
        "Config is empty",
    "Критическая ошибка":
        "Critical error",
    "Найдено {0} источников.\nНажмите «Сохранить config.yaml» для создания файла.":
        "Found {0} sources.\nClick “Save config.yaml” to create the file.",
    "Настройки загружены и применены.":
        "Settings loaded and applied.",
    "Настройки загружены. Нажмите «Сохранить» для применения.":
        "Settings loaded. Click “Save” to apply them.",
    "Настройки сброшены. Нажмите «Сохранить» для применения.":
        "Settings reset. Click “Save” to apply them.",
    "Настройки сохранены в:\n{0}":
        "Settings saved to:\n{0}",
    "Настройки сохранены и будут применены к следующим запускам.":
        "Settings were saved and will apply to the next runs.",
    "Не выбраны файлы":
        "No files selected",
    "Не найдено ни одного источника. Проверьте параметры или файл.":
        "No sources found. Check the parameters or the file.",
    "Не удалось выполнить авто-поиск:\n\n{0}\n\n{1}":
        "Auto-discovery failed:\n\n{0}\n\n{1}",
    "Не удалось загрузить файл:\n{0}":
        "Could not load the file:\n{0}",
    "Не удалось объединить конфиги:\n\n{0}":
        "Could not merge the configs:\n\n{0}",
    "Не удалось открыть мастер создания config.yaml:\n\n{0}\n\nПодробности:\n{1}":
        "Could not open the config.yaml wizard:\n\n{0}\n\nDetails:\n{1}",
    "Не удалось открыть настройки:\n\n{0}\n\n{1}":
        "Could not open settings:\n\n{0}\n\n{1}",
    "Не удалось открыть файл:\n{0}":
        "Could not open the file:\n{0}",
    "Не удалось применить обновление:\n{0}":
        "Could not apply the update:\n{0}",
    "Не удалось сгенерировать config.yaml:\n\n{0}\n\nПодробности:\n{1}":
        "Could not generate config.yaml:\n\n{0}\n\nDetails:\n{1}",
    "Недостаточно файлов":
        "Not enough files",
    "Нет данных":
        "No data",
    "Нет конфигурации":
        "No configuration",
    "Новый коммит: {0}\nАвтор: {1}\nДата: {2}\nСообщение: {3}\n\nПрименить обновление?":
        "New commit: {0}\nAuthor: {1}\nDate: {2}\nMessage: {3}\n\nApply the update?",
    "О программе":
        "About",
    "Обновление из коммита":
        "Update from commit",
    "Обновление применено":
        "Update applied",
    "Обновления":
        "Updates",
    "Объединение завершено":
        "Merge finished",
    "Один или оба файла не существуют.":
        "One or both files do not exist.",
    "Ошибка":
        "Error",
    "Ошибка YAML":
        "YAML error",
    "Ошибка генерации":
        "Generation error",
    "Ошибка конфигурации":
        "Configuration error",
    "Ошибка настроек":
        "Settings error",
    "Ошибка обновления":
        "Update error",
    "Ошибка объединения":
        "Merge error",
    "Ошибка проверки":
        "Check failed",
    "Ошибка сохранения":
        "Save error",
    "Ошибка чтения":
        "Read error",
    "Ошибка экспорта":
        "Export error",
    "Папка не найдена":
        "Folder not found",
    "Поиск завершён":
        "Search finished",
    "Поиск уже идёт.":
        "Search is already running.",
    "Проверка":
        "Check",
    "Профиль выбран":
        "Profile selected",
    "Профиль: {0}\nТип: {1}\nКатегории: {2}\n\nПрофиль будет применён при запуске краулинга.":
        "Profile: {0}\nType: {1}\nCategories: {2}\n\nThe profile will be used when crawling starts.",
    "Пусто":
        "Empty",
    "Сброс настроек":
        "Reset settings",
    "Сбросить все настройки к значениям по умолчанию?":
        "Reset all settings to their defaults?",
    "Сброшено":
        "Reset done",
    "Синтаксис корректен.\nВерхнеуровневые ключи: {0}":
        "Syntax is correct.\nTop-level keys: {0}",
    "Сначала выберите config.yaml":
        "Choose a config.yaml first",
    "Создан config.yaml с {0} источниками.\n\nФайл: {1}\n\nЗагрузить его в главное окно?":
        "config.yaml created with {0} sources.\n\nFile: {1}\n\nLoad it into the main window?",
    "Создан config.yaml с {0} источниками.\nФайл: {1}":
        "config.yaml created with {0} sources.\nFile: {1}",
    "Сохранено":
        "Saved",
    "Сохранено источников: {0}\nФайл: {1}\n\nТеперь можно загрузить этот config.yaml в главное окно и запустить краулинг.":
        "Sources saved: {0}\nFile: {1}\n\nYou can now load this config.yaml into the main window and start crawling.",
    "Тема изменена":
        "Theme changed",
    "Тема изменена на \"{0}\". Перезапустите приложение для применения.":
        "Theme changed to \"{0}\". Restart the app to apply it.",
    "У вас последняя версия (все коммиты применены).":
        "You are up to date (all commits applied).",
    "Укажите оба файла корпусов.":
        "Specify both corpus files.",
    "Укажите путь к Excel/CSV-файлу на вкладке «Excel / CSV».":
        "Enter the path to an Excel/CSV file on the “Excel / CSV” tab.",
    "Укажите существующую папку.\nТекущее значение: {0}":
        "Enter an existing folder.\nCurrent value: {0}",
    "Укажите файл корпуса.":
        "Specify the corpus file.",
    "Укажите хотя бы один topic (например, kicad, pcb)":
        "Enter at least one topic (e.g. kicad, pcb)",
    "Укажите хотя бы один тег (например, kicad, pcb, stm32)":
        "Enter at least one tag (e.g. kicad, pcb, stm32)",
    "Укажите хотя бы одну категорию (например: Electronics)":
        "Enter at least one category (e.g. Electronics)",
    "Укажите хотя бы одну тему/тег/категорию.\nИли выберите пресет из списка.":
        "Enter at least one topic/tag/category.\nOr pick a preset from the list.",
    "Уникальных источников: {0}\nДубликатов удалено: {1}\n\nНажмите «Сохранить» для создания файла.":
        "Unique sources: {0}\nDuplicates removed: {1}\n\nClick “Save” to create the file.",
    "Успешно обновлено .py файлов: {0}\nОшибок: {1}\n\nПожалуйста, перезапустите CorpusBuilder\nдля применения изменений.":
        "Updated .py files: {0}\nErrors: {1}\n\nPlease restart CorpusBuilder\nfor the changes to take effect.",
    "Файл не выбран":
        "No file selected",
    "Файл сохранён:\n{0}":
        "File saved:\n{0}",
    "Файл сохранён: {0}\n\nИсточников: {1}":
        "File saved: {0}\n\nSources: {1}",
    "Файлы не найдены":
        "Files not found",
    "Шаблон создан":
        "Template created",
    "Шаблон сохранён:\n{0}\n\nОткройте его в Excel, заполните колонки url и depth, сохраните и загрузите обратно.":
        "Template saved:\n{0}\n\nOpen it in Excel, fill the url and depth columns, save and load it back.",
    "Экспорт завершён":
        "Export finished",
    "Экспортировано":
        "Exported",
}

# fmt: on
