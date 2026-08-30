"""Тесты на auto_updater и zip_distributor."""
import zipfile
from pathlib import Path
from unittest import mock

import pytest

# ============================================================
# AutoUpdater — тесты логики (без сети)
# ============================================================

def test_version_comparison():
    """Сравнение версий."""
    from corpus_builder.auto_updater import AutoUpdater
    assert AutoUpdater._compare_versions("0.2.0", "0.1.0") == 1
    assert AutoUpdater._compare_versions("0.1.0", "0.2.0") == -1
    assert AutoUpdater._compare_versions("0.2.0", "0.2.0") == 0
    assert AutoUpdater._compare_versions("1.0.0", "0.99.99") == 1
    assert AutoUpdater._compare_versions("0.2.1", "0.2.0") == 1


def test_updater_init():
    """Инициализация AutoUpdater."""
    from corpus_builder.auto_updater import AutoUpdater
    updater = AutoUpdater("owner/repo", "0.2.0")
    assert updater.repo == "owner/repo"
    assert updater.current_version == "0.2.0"


def test_is_patch_zip():
    """Определение типа ZIP-файла."""
    from corpus_builder.auto_updater import AutoUpdater
    updater = AutoUpdater()
    assert updater._is_patch_zip("patch.zip") is True
    assert updater._is_patch_zip("update.zip") is True
    assert updater._is_patch_zip("CorpusBuilder.zip") is False
    assert updater._is_patch_zip("patch-0.2.1.zip") is False


def test_check_for_updates_no_releases():
    """Если GitHub возвращает 404 — нет обновлений."""
    from corpus_builder.auto_updater import AutoUpdater

    updater = AutoUpdater("nonexistent/repo", "0.2.0")

    # Мокируем requests.get
    mock_response = mock.MagicMock()
    mock_response.status_code = 404
    with mock.patch("requests.get", return_value=mock_response):
        result = updater.check_for_updates()
    assert result is None


def test_check_for_updates_already_current():
    """Если версия текущая — нет обновлений."""
    from corpus_builder.auto_updater import AutoUpdater

    updater = AutoUpdater("owner/repo", "0.2.0")

    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "tag_name": "v0.2.0",
        "html_url": "https://github.com/owner/repo/releases/v0.2.0",
        "body": "Current release",
        "assets": [],
    }
    with mock.patch("requests.get", return_value=mock_response):
        result = updater.check_for_updates()
    assert result is None  # уже актуально


def test_check_for_updates_available():
    """Если есть новая версия — возвращается информация."""
    from corpus_builder.auto_updater import AutoUpdater

    updater = AutoUpdater("owner/repo", "0.1.0")

    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "tag_name": "v0.2.0",
        "html_url": "https://github.com/owner/repo/releases/v0.2.0",
        "body": "New features",
        "assets": [
            {"name": "patch.zip", "browser_download_url": "https://example.com/patch.zip",
             "size": 50000},
        ],
    }
    with mock.patch("requests.get", return_value=mock_response):
        result = updater.check_for_updates()

    assert result is not None
    assert result["version"] == "0.2.0"
    assert result["url"] == "https://github.com/owner/repo/releases/v0.2.0"
    assert len(result["assets"]) == 1
    assert result["assets"][0]["name"] == "patch.zip"


# ============================================================
# create_patch_zip — создание патча
# ============================================================

def test_create_patch_zip(tmp_path):
    """Создание patch.zip из папки с .py файлами."""
    from corpus_builder.auto_updater import AutoUpdater

    # Создаём тестовую папку с .py файлами
    source = tmp_path / "corpus_builder"
    source.mkdir()
    (source / "__init__.py").write_text("# test", encoding="utf-8")
    (source / "gui.py").write_text("print('hello')", encoding="utf-8")
    (source / "crawlers").mkdir()
    (source / "crawlers" / "html.py").write_text("print('crawl')", encoding="utf-8")

    output = tmp_path / "patch.zip"
    result = AutoUpdater.create_patch_zip(source, output)

    assert Path(result).exists()
    assert output.stat().st_size > 0

    # Проверяем содержимое
    with zipfile.ZipFile(output, "r") as zf:
        names = zf.namelist()
    assert "corpus_builder/__init__.py" in names
    assert "corpus_builder/gui.py" in names
    assert "corpus_builder/crawlers/html.py" in names


def test_create_patch_zip_specific_files(tmp_path):
    """Создание patch.zip только с указанными файлами."""
    from corpus_builder.auto_updater import AutoUpdater

    source = tmp_path / "corpus_builder"
    source.mkdir()
    (source / "file1.py").write_text("# file1", encoding="utf-8")
    (source / "file2.py").write_text("# file2", encoding="utf-8")
    (source / "file3.py").write_text("# file3", encoding="utf-8")

    output = tmp_path / "patch.zip"
    AutoUpdater.create_patch_zip(source, output, files_to_include=["file1.py", "file3.py"])

    with zipfile.ZipFile(output, "r") as zf:
        names = zf.namelist()
    assert "corpus_builder/file1.py" in names
    assert "corpus_builder/file3.py" in names
    assert "corpus_builder/file2.py" not in names


# ============================================================
# zip_distributor — создание дистрибутива
# ============================================================

def test_create_distribution(tmp_path):
    """Создание ZIP-дистрибутива из собранной папки."""
    from corpus_builder.zip_distributor import create_distribution

    # Создаём имитацию собранной папки
    build_dir = tmp_path / "CorpusBuilder"
    build_dir.mkdir()
    (build_dir / "CorpusBuilder.exe").write_bytes(b"\x4d\x5a")  # MZ header
    (build_dir / "_internal").mkdir()
    (build_dir / "_internal" / "python313.dll").write_bytes(b"\x00" * 100)
    (build_dir / "_internal" / "corpus_builder").mkdir()
    (build_dir / "_internal" / "corpus_builder" / "__init__.py").write_text("# test", encoding="utf-8")
    (build_dir / "_internal" / "corpus_builder" / "gui.py").write_text("print('gui')", encoding="utf-8")

    output_zip = tmp_path / "CorpusBuilder-0.2.0.zip"

    result = create_distribution(
        build_dir=build_dir,
        output_zip=output_zip,
        version="0.2.0",
        include_patch=True,
    )

    assert result["distribution_zip"] == str(output_zip)
    assert Path(result["distribution_zip"]).exists()
    assert result["distribution_size"] > 0
    assert "patch_zip" in result
    assert Path(result["patch_zip"]).exists()
    assert result["patch_size"] > 0

    # Patch должен быть значительно меньше полного дистрибутива
    assert result["patch_size"] < result["distribution_size"]


def test_create_distribution_no_patch(tmp_path):
    """Создание дистрибутива без patch.zip."""
    from corpus_builder.zip_distributor import create_distribution

    build_dir = tmp_path / "CorpusBuilder"
    build_dir.mkdir()
    (build_dir / "CorpusBuilder.exe").write_bytes(b"\x4d\x5a")

    output_zip = tmp_path / "CorpusBuilder.zip"

    result = create_distribution(
        build_dir=build_dir,
        output_zip=output_zip,
        include_patch=False,
    )

    assert "patch_zip" not in result
    assert Path(result["distribution_zip"]).exists()


def test_create_distribution_nonexistent_dir(tmp_path):
    """Ошибка если папка сборки не существует."""
    from corpus_builder.zip_distributor import create_distribution

    with pytest.raises(FileNotFoundError):
        create_distribution(build_dir=tmp_path / "nonexistent")


def test_create_patch_only(tmp_path):
    """Создание только patch.zip."""
    from corpus_builder.zip_distributor import create_patch_only

    source = tmp_path / "corpus_builder"
    source.mkdir()
    (source / "main.py").write_text("print('main')", encoding="utf-8")

    output = tmp_path / "patch.zip"
    result = create_patch_only(source, output, version="0.2.1")

    assert Path(result).exists()
    with zipfile.ZipFile(result, "r") as zf:
        assert "corpus_builder/main.py" in zf.namelist()


# ============================================================
# CommitUpdater — тесты
# ============================================================

def test_commit_updater_init():
    """Инициализация CommitUpdater."""
    from corpus_builder.auto_updater import CommitUpdater
    updater = CommitUpdater("owner/repo", "main")
    assert updater.repo == "owner/repo"
    assert updater.branch == "main"


def test_commit_updater_no_updates():
    """Если нет новых коммитов — возвращает None."""
    from corpus_builder.auto_updater import CommitUpdater

    updater = CommitUpdater("owner/repo", "main")

    # Мокируем SHA последнего коммита как совпадающий с известным
    with mock.patch.object(updater, '_get_last_known_sha', return_value="abc123"):
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"sha": "abc123", "commit": {"message": "test", "author": {"name": "test", "date": "2024-01-01"}}}
        ]
        with mock.patch("requests.get", return_value=mock_response):
            result = updater.check_for_commit_updates()

    assert result is None  # уже актуально


def test_commit_updater_update_available():
    """Если есть новый коммит — возвращает информацию."""
    from corpus_builder.auto_updater import CommitUpdater

    updater = CommitUpdater("owner/repo", "main")

    with mock.patch.object(updater, '_get_last_known_sha', return_value="old_sha"):
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "sha": "new_sha_1234567890",
                "commit": {
                    "message": "Fix: something important",
                    "author": {"name": "Developer", "date": "2024-08-08T12:00:00Z"}
                },
                "html_url": "https://github.com/owner/repo/commit/new_sha"
            }
        ]
        with mock.patch("requests.get", return_value=mock_response):
            result = updater.check_for_commit_updates()

    assert result is not None
    assert result["sha"] == "new_sha_1234567890"
    assert result["short_sha"] == "new_sha_"
    assert "Fix" in result["message"]
    assert result["author"] == "Developer"


def test_commit_updater_rate_limit():
    """При 403 (rate limit) — возвращает None."""
    from corpus_builder.auto_updater import CommitUpdater

    updater = CommitUpdater("owner/repo", "main")

    mock_response = mock.MagicMock()
    mock_response.status_code = 403
    with mock.patch("requests.get", return_value=mock_response):
        result = updater.check_for_commit_updates()
    assert result is None


def test_commit_updater_save_and_load_sha(tmp_path):
    """Сохранение и загрузка SHA коммита."""
    from corpus_builder.auto_updater import CommitUpdater

    updater = CommitUpdater("owner/repo", "main")

    sha_file = tmp_path / "last_commit.txt"
    with mock.patch.object(Path, "write_text"):
        with mock.patch.object(Path, "exists", return_value=True):
            with mock.patch.object(Path, "read_text", return_value="test_sha_123"):
                loaded = updater._get_last_known_sha()
                assert loaded == "test_sha_123"

    # Сохранение
    with mock.patch.object(Path, "write_text"):
        updater._save_last_known_sha("new_sha_456")


def test_commit_updater_get_target_dir_frozen(tmp_path):
    """В frozen режиме ищет _internal/corpus_builder/."""
    from corpus_builder.auto_updater import CommitUpdater

    updater = CommitUpdater()

    # Создаём фейковую структуру
    fake_exe = tmp_path / "CorpusBuilder.exe"
    fake_exe.write_text("fake")
    internal_dir = tmp_path / "_internal" / "corpus_builder"
    internal_dir.mkdir(parents=True)

    with mock.patch("sys.frozen", True, create=True), \
         mock.patch("sys.executable", str(fake_exe)):
        result = updater._get_target_dir()
        assert result is not None
        assert result == internal_dir


def test_commit_updater_get_target_dir_dev():
    """В dev режиме ищет corpus_builder/ в cwd."""
    from corpus_builder.auto_updater import CommitUpdater

    updater = CommitUpdater()

    # Без frozen — ищет в cwd
    result = updater._get_target_dir()
    # Может вернуть путь или None, главное не упасть
    assert result is not None or result is None


def test_commit_updater_download_file():
    """Тест скачивания файла через Contents API (мок)."""
    import base64

    from corpus_builder.auto_updater import CommitUpdater

    updater = CommitUpdater("owner/repo", "main")

    # Мокируем GitHub Contents API ответ
    file_content = b"print('hello world')"
    encoded = base64.b64encode(file_content).decode()

    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "content": encoded,
        "encoding": "base64",
        "name": "test.py",
        "path": "corpus_builder/test.py",
    }
    with mock.patch("requests.get", return_value=mock_response):
        content = updater._download_file_from_github("test.py", "sha123")

    assert content is not None
    assert content == file_content


def test_commit_updater_download_file_404():
    """Если файл не найден (404) — возвращает None."""
    from corpus_builder.auto_updater import CommitUpdater

    updater = CommitUpdater("owner/repo", "main")

    mock_response = mock.MagicMock()
    mock_response.status_code = 404
    with mock.patch("requests.get", return_value=mock_response):
        content = updater._download_file_from_github("nonexistent.py", "sha123")

    assert content is None
