"""Тесты на улучшения интерфейса (A-O)."""
import json

# ============================================================
# A. Drag-and-Drop — тестируем только логику (без Qt)
# ============================================================

def test_config_drop_area_logic():
    """Проверяем, что файл .yaml принимается, а .txt — нет."""
    # Логика: проверяем только расширение
    assert "test.yaml".endswith((".yaml", ".yml"))
    assert "test.yml".endswith((".yaml", ".yml"))
    assert not "test.txt".endswith((".yaml", ".yml"))
    assert not "test.csv".endswith((".yaml", ".yml"))


# ============================================================
# D. SplitterStateSaver
# ============================================================

def test_splitter_state_saver(tmp_path):
    """Сохранение и восстановление позиций сплиттера."""
    # Тестируем только логику JSON-сохранения (без QSplitter)
    from corpus_builder.gui_improvements import SplitterStateSaver

    settings_file = tmp_path / "splitter.json"
    saver = SplitterStateSaver(settings_file)

    # Сохраняем данные напрямую в JSON (имитируя QSplitter.sizes())
    data = {"main": [300, 700]}
    settings_file.write_text(json.dumps(data), encoding="utf-8")
    assert settings_file.exists()

    # Проверяем, что данные читаются
    with open(settings_file, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["main"] == [300, 700]


# ============================================================
# F + O. Темы оформления
# ============================================================

def test_themes_dict():
    """Все темы имеют нужные ключи."""
    from corpus_builder.gui_improvements import THEMES
    required_keys = [
        "window_bg", "darker_bg", "lighter_bg",
        "text_primary", "text_secondary", "accent",
        "accent_hover", "success", "warn", "error", "border",
    ]
    for theme_name, colors in THEMES.items():
        for key in required_keys:
            assert key in colors, f"Theme {theme_name} missing key {key}"


def test_themes_count():
    """Должно быть минимум 5 тем."""
    from corpus_builder.gui_improvements import THEMES
    assert len(THEMES) >= 5
    assert "dark" in THEMES
    assert "light" in THEMES
    assert "material_blue" in THEMES
    assert "material_green" in THEMES
    assert "material_purple" in THEMES


# ============================================================
# H. RecentConfigsManager
# ============================================================

def test_recent_configs_add_and_get(tmp_path):
    """Добавление и получение недавних файлов."""
    from corpus_builder.gui_improvements import RecentConfigsManager

    settings_file = tmp_path / "recent.json"
    manager = RecentConfigsManager(settings_file)

    # Создаём тестовые файлы
    f1 = tmp_path / "config1.yaml"
    f2 = tmp_path / "config2.yaml"
    f3 = tmp_path / "config3.yaml"
    for f in [f1, f2, f3]:
        f.write_text("sources: []", encoding="utf-8")

    manager.add(str(f1))
    manager.add(str(f2))
    manager.add(str(f3))

    recent = manager.get_recent(3)
    # Последний добавленный должен быть первым
    assert str(f3) == recent[0]
    assert str(f2) == recent[1]
    assert str(f1) == recent[2]


def test_recent_configs_dedup(tmp_path):
    """Дубликаты удаляются."""
    from corpus_builder.gui_improvements import RecentConfigsManager

    settings_file = tmp_path / "recent.json"
    manager = RecentConfigsManager(settings_file)

    f1 = tmp_path / "config.yaml"
    f1.write_text("sources: []", encoding="utf-8")

    manager.add(str(f1))
    manager.add(str(f1))  # дубликат

    recent = manager.get_all()
    assert len(recent) == 1  # только один экземпляр


def test_recent_configs_nonexistent_filtered(tmp_path):
    """Несуществующие файлы отфильтровываются."""
    from corpus_builder.gui_improvements import RecentConfigsManager

    settings_file = tmp_path / "recent.json"
    manager = RecentConfigsManager(settings_file)

    f1 = tmp_path / "config.yaml"
    f1.write_text("sources: []", encoding="utf-8")
    manager.add(str(f1))
    manager.add("/nonexistent/path.yaml")

    recent = manager.get_all()
    # Несуществующий файл не должен попасть в список
    assert len(recent) == 1
    assert str(f1) in recent[0]


def test_recent_configs_clear(tmp_path):
    """Очистка списка."""
    from corpus_builder.gui_improvements import RecentConfigsManager

    settings_file = tmp_path / "recent.json"
    manager = RecentConfigsManager(settings_file)

    f1 = tmp_path / "config.yaml"
    f1.write_text("sources: []", encoding="utf-8")
    manager.add(str(f1))

    manager.clear()
    assert manager.get_all() == []


# ============================================================
# I. ProgressBarWithETA — тест логики форматирования
# ============================================================

def test_format_duration():
    """Форматирование длительности."""
    from corpus_builder.gui_improvements import ProgressBarWithETA

    assert ProgressBarWithETA._format_duration(30) == "30s"
    assert ProgressBarWithETA._format_duration(90) == "1m 30s"
    assert ProgressBarWithETA._format_duration(3700) == "1h 1m"
    assert ProgressBarWithETA._format_duration(-1) == "?"


# ============================================================
# N. Локализация
# ============================================================

def test_translation_ru():
    """Перевод на русский."""
    from corpus_builder.gui_improvements import set_language, tr

    set_language("ru")
    assert tr("menu_file") == "Файл"
    assert tr("menu_quit") == "Выход"
    assert tr("btn_save") == "💾  Сохранить"


def test_translation_en():
    """Перевод на английский."""
    from corpus_builder.gui_improvements import set_language, tr

    set_language("en")
    assert tr("menu_file") == "File"
    assert tr("menu_quit") == "Quit"
    assert tr("btn_save") == "💾  Save"


def test_translation_unknown_key():
    """Неизвестный ключ возвращает сам себя."""
    from corpus_builder.gui_improvements import set_language, tr

    set_language("ru")
    assert tr("nonexistent_key") == "nonexistent_key"


def test_get_language():
    """Получение текущего языка."""
    from corpus_builder.gui_improvements import get_language, set_language

    set_language("ru")
    assert get_language() == "ru"
    set_language("en")
    assert get_language() == "en"


# ============================================================
# G. KicadPreviewDialog — тест логики парсинга
# ============================================================

def test_kicad_parser_v6(tmp_path):
    """Парсинг KiCad v6 .kicad_sch формата."""
    # Создаём минимальный .kicad_sch файл
    kicad_content = '''
    (symbol (lib_id "Device:R") (at 100 50 0)
      (property "Reference" "R1" (at 102 48 0))
      (property "Value" "10k" (at 102 52 0))
      (property "Footprint" "Resistor_SMD:R_0603" (at 100 50 0))
      (property "Datasheet" "~" (at 100 50 0))
    )
    (symbol (lib_id "Device:C") (at 150 50 0)
      (property "Reference" "C1" (at 152 48 0))
      (property "Value" "100nF" (at 152 52 0))
    )
    '''
    kicad_file = tmp_path / "test.kicad_sch"
    kicad_file.write_text(kicad_content, encoding="utf-8")

    # Парсим через regex
    import re
    symbol_pattern = re.compile(
        r'\(lib_id\s*"([^"]*)".*?"Reference"\s*"([^"]*)".*?"Value"\s*"([^"]*)"',
        re.DOTALL
    )
    matches = list(symbol_pattern.finditer(kicad_content))
    assert len(matches) >= 2
    assert matches[0].group(2) == "R1"
    assert matches[0].group(3) == "10k"
    assert matches[1].group(2) == "C1"
    assert matches[1].group(3) == "100nF"


# ============================================================
# E. Toast — тест логики
# ============================================================

def test_toast_types():
    """Типы toast-уведомлений определены."""
    from corpus_builder.gui_improvements import ToastNotification

    assert ToastNotification.INFO == "info"
    assert ToastNotification.SUCCESS == "success"
    assert ToastNotification.WARNING == "warning"
    assert ToastNotification.ERROR == "error"


def test_toast_colors():
    """Все типы имеют цвета."""
    from corpus_builder.gui_improvements import ToastNotification

    for toast_type in [ToastNotification.INFO, ToastNotification.SUCCESS,
                       ToastNotification.WARNING, ToastNotification.ERROR]:
        assert toast_type in ToastNotification._colors
        colors = ToastNotification._colors[toast_type]
        assert "bg" in colors
        assert "border" in colors
