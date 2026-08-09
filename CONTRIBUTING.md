# Contributing to CorpusBuilder

Спасибо за интерес к проекту CorpusBuilder! Любой вклад приветствуется.

## 📋 Содержание
- [Как внести вклад](#как-внести-вклад)
- [Сообщить об ошибке](#сообщить-об-ошибке)
- [Предложить улучшение](#предложить-улучшение)
- [Разработка](#разработка)
- [Стиль кода](#стиль-кода)
- [Тесты](#тесты)
- [Лицензия](#лицензия)

## Как внести вклад

1. **Fork** репозитория
2. Создайте ветку для вашей фичи: `git checkout -b feature/amazing-feature`
3. Внесите изменения и закоммитьте: `git commit -m 'Add amazing feature'`
4. Запушьте ветку: `git push origin feature/amazing-feature`
5. Откройте **Pull Request**

## Сообщить об ошибке

Откройте [Issue](https://github.com/draco74-glitch/corpus_builder/issues/new) и опишите:

- **Версия Python** (3.13 или другая)
- **ОС** (Windows 10/11, Linux, macOS)
- **Как запускали** (CLI / GUI / собранный .exe)
- **Полный traceback** ошибки
- **Шаги для воспроизведения**

## Предложить улучшение

Откройте [Issue](https://github.com/draco74-glitch/corpus_builder/issues/new) с меткой `enhancement` и опишите:

- Какую проблему решает ваше предложение
- Как вы видите решение
- Альтернативы, которые вы рассматривали

## Разработка

### Установка dev-окружения

```bash
git clone https://github.com/draco74-glitch/corpus_builder.git
cd corpus_builder
python3.13 -m venv .venv
source .venv/bin/activate    # Linux/macOS
.venv\Scripts\activate       # Windows

pip install -r requirements.txt
pip install -e .[dev,gui,build]
```

### Структура проекта

```
corpus_builder/
├── corpus_builder/           # Python-пакет
│   ├── crawlers/             # Краулеры (HTML, PDF, GitHub, SE, academic)
│   ├── postproc/             # Пост-обработка (dedup, quality, normalize)
│   ├── gui.py                # Главное окно GUI
│   ├── config_generator.py   # Генератор config.yaml
│   ├── pipeline.py           # Оркестратор
│   └── ...
├── tests/                    # Unit-тесты (166+ тестов)
├── CorpusBuilder.spec        # PyInstaller one-dir spec
├── build.bat / build.sh      # Скрипты сборки
└── README.md
```

## Стиль кода

- Python 3.10+ (type hints, `match/case`, `|` вместо `Union`)
- `ruff` для линтинга: `ruff check corpus_builder/`
- Максимальная длина строки: 100 символов
- Docstrings на русском языке (как в существующем коде)
- Имена функций: `snake_case`
- Имена классов: `PascalCase`

## Тесты

```bash
# Запуск всех тестов
pytest tests/ -v

# Запуск конкретного модуля
pytest tests/test_quality_filters.py -v

# С покрытием
pytest tests/ --cov=corpus_builder --cov-report=html
```

**Перед PR убедитесь, что все тесты проходят:**
```bash
pytest tests/ -q --ignore=tests/test_vcr_cassettes.py
```

## Лицензия

Внося вклад в проект, вы соглашаетесь, что ваш код будет распространяться
под [некоммерческой лицензией](LICENSE) проекта. Любой коммерческий
использование требует отдельного разрешения от автора.
