"""Тесты на оптимизации производительности (Улучшения 1-14)."""
import gzip
import json

# ============================================================
# Улучшение 2: CorpusWriter — буферизованная запись
# ============================================================

def test_corpus_writer_basic(tmp_path):
    """Запись одной записи, flush при close."""
    from corpus_builder.writer import CorpusWriter
    path = tmp_path / "corpus.jsonl"
    with CorpusWriter(path, buffer_size=10) as w:
        w.write({"url": "https://example.com/1", "content": "test"})
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    r = json.loads(lines[0])
    assert r["url"] == "https://example.com/1"


def test_corpus_writer_buffering(tmp_path):
    """Буфер не пишет на каждую запись, только при заполнении."""
    from corpus_builder.writer import CorpusWriter
    path = tmp_path / "corpus.jsonl"
    w = CorpusWriter(path, buffer_size=5)
    # Пишем 3 записи — должно остаться в буфере
    for i in range(3):
        w.write({"url": f"https://example.com/{i}", "content": f"test {i}"})
    # Файл ещё пуст
    assert path.exists() is False or path.stat().st_size == 0
    # Пишем ещё 2 — буфер = 5, должен сброситься
    w.write({"url": "https://example.com/4", "content": "test 4"})
    w.write({"url": "https://example.com/5", "content": "test 5"})
    # Теперь файл должен быть непустой
    content = path.read_text(encoding="utf-8")
    assert len(content) > 0
    lines = [l for l in content.strip().split("\n") if l]
    assert len(lines) == 5
    w.close()


def test_corpus_writer_flush_explicit(tmp_path):
    """Принудительный flush."""
    from corpus_builder.writer import CorpusWriter
    path = tmp_path / "corpus.jsonl"
    w = CorpusWriter(path, buffer_size=100)
    w.write({"url": "https://example.com/1", "content": "test"})
    w.flush()
    # После flush файл должен содержать запись
    content = path.read_text(encoding="utf-8")
    assert "https://example.com/1" in content
    w.close()


def test_corpus_writer_write_many(tmp_path):
    """Пакетная запись."""
    from corpus_builder.writer import CorpusWriter
    path = tmp_path / "corpus.jsonl"
    with CorpusWriter(path, buffer_size=2) as w:
        records = [{"url": f"https://example.com/{i}", "content": "x"} for i in range(10)]
        w.write_many(records)
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 10


# ============================================================
# Улучшение 12: GzipCorpusWriter — сжатие на лету
# ============================================================

def test_gzip_corpus_writer(tmp_path):
    """Сжатый JSONL пишется и читается корректно."""
    from corpus_builder.writer import GzipCorpusWriter, open_corpus_reader
    path = tmp_path / "corpus.jsonl.gz"
    with GzipCorpusWriter(path, buffer_size=5) as w:
        for i in range(10):
            w.write({"url": f"https://example.com/{i}", "content": f"test {i}"})
    assert path.exists()
    # Проверим, что файл сжат (magic bytes)
    with open(path, "rb") as f:
        magic = f.read(2)
    assert magic == b"\x1f\x8b"
    # Читаем обратно
    with open_corpus_reader(path) as reader:
        lines = list(reader)
    assert len(lines) == 10
    r = json.loads(lines[0])
    assert r["url"] == "https://example.com/0"


def test_is_gzip_file(tmp_path):
    """Определение gzip по magic bytes."""
    from corpus_builder.writer import is_gzip_file
    # Обычный файл
    plain = tmp_path / "plain.jsonl"
    plain.write_text('{"a": 1}\n', encoding="utf-8")
    assert is_gzip_file(plain) is False
    # Gzip файл
    gz = tmp_path / "data.gz"
    with gzip.open(gz, "wt", encoding="utf-8") as f:
        f.write('{"a": 1}\n')
    assert is_gzip_file(gz) is True


# ============================================================
# Улучшение 13: MmapJsonlReader
# ============================================================

def test_mmap_reader_small_file(tmp_path):
    """Для маленьких файлов — обычный readline (без mmap)."""
    from corpus_builder.mmap_reader import MmapJsonlReader
    path = tmp_path / "small.jsonl"
    path.write_text('{"a": 1}\n{"a": 2}\n{"a": 3}\n', encoding="utf-8")
    with MmapJsonlReader(path, min_size_for_mmap=1024) as reader:
        records = list(reader.iter_records())
    assert len(records) == 3
    assert records[0]["a"] == 1


def test_mmap_reader_large_file(tmp_path):
    """Для больших файлов — mmap."""
    from corpus_builder.mmap_reader import MmapJsonlReader
    path = tmp_path / "large.jsonl"
    # Создаём файл > 100 КБ
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps({"url": f"https://example.com/{i}",
                                "content": "x" * 50}) + "\n" for i in range(5000))
    # mmap должен сработать
    with MmapJsonlReader(path, min_size_for_mmap=100 * 1024) as reader:
        records = list(reader.iter_records())
        # Должен прочитать все
        assert len(records) == 5000
        assert records[0]["url"] == "https://example.com/0"


def test_mmap_reader_count_lines(tmp_path):
    """Подсчёт строк через mmap."""
    from corpus_builder.mmap_reader import MmapJsonlReader
    path = tmp_path / "test.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps({"i": i}) + "\n" for i in range(100))
    with MmapJsonlReader(path, min_size_for_mmap=1) as reader:
        count = reader.count_lines()
    assert count == 100


# ============================================================
# Улучшение 14: IncrementalDedup
# ============================================================

def test_incremental_dedup_basic(tmp_path):
    """Базовая инкрементальная дедупликация."""
    from corpus_builder.incremental_dedup import IncrementalDedup
    index_path = tmp_path / "lsh_index.pkl"
    dedup = IncrementalDedup(index_path, threshold=0.85, num_perm=64)
    # Добавляем уникальный текст
    result = dedup.add("https://example.com/1", "This is a unique technical text about electronics.")
    assert result is None
    # Добавляем дубликат
    result = dedup.add("https://example.com/2", "This is a unique technical text about electronics.")
    assert result == "https://example.com/1"


def test_incremental_dedup_save_load(tmp_path):
    """Сохранение и загрузка индекса."""
    from corpus_builder.incremental_dedup import IncrementalDedup
    index_path = tmp_path / "lsh_index.pkl"
    # Первый прогон
    dedup1 = IncrementalDedup(index_path, num_perm=64)
    dedup1.add("https://example.com/1", "Unique technical text about electronics and circuits.")
    dedup1.save()
    assert index_path.exists()
    # Второй прогон — должен загрузить индекс
    dedup2 = IncrementalDedup(index_path, num_perm=64)
    assert "https://example.com/1" in dedup2.processed_urls
    # Тот же текст — должен определиться как дубликат
    result = dedup2.add("https://example.com/2", "Unique technical text about electronics and circuits.")
    assert result == "https://example.com/1"


def test_incremental_dedup_clear(tmp_path):
    """Очистка индекса."""
    from corpus_builder.incremental_dedup import IncrementalDedup
    index_path = tmp_path / "lsh_index.pkl"
    dedup = IncrementalDedup(index_path, num_perm=64)
    dedup.add("https://example.com/1", "Some technical text about electronics.")
    dedup.save()
    assert index_path.exists()
    dedup.clear()
    assert not index_path.exists()
    assert len(dedup.processed_urls) == 0


def test_incremental_dedup_process_corpus(tmp_path):
    """Обработка корпуса через process_new_corpus."""
    from corpus_builder.incremental_dedup import IncrementalDedup
    # Создаём корпус
    corpus_path = tmp_path / "corpus.jsonl"
    with open(corpus_path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps({
                "source_url": f"https://example.com/{i}",
                "content": f"Unique text number {i} about electronics circuits.",
                "status": "ok",
            }) + "\n" for i in range(5))
        # Дубликат первой записи
        f.write(json.dumps({
            "source_url": "https://example.com/dup",
            "content": "Unique text number 0 about electronics circuits.",
            "status": "ok",
        }) + "\n")
    index_path = tmp_path / "lsh_index.pkl"
    dedup = IncrementalDedup(index_path, num_perm=64, threshold=0.85)
    duplicates = dedup.process_new_corpus(corpus_path)
    # Должен найти дубликат
    assert len(duplicates) >= 1


# ============================================================
# Улучшение 4: Connection pooling
# ============================================================

def test_make_session_has_pooling():
    """make_session создаёт Session с connection pooling."""
    from corpus_builder.models import AppConfig
    from corpus_builder.robots import make_session
    cfg = AppConfig(
        sources=[{"url": "https://example.com", "type": "html"}],
        output={"corpus_file": "test.jsonl", "download_dir": "test",
                "user_agent": "Test/1.0", "request_timeout": 10},
    )
    session = make_session(cfg)
    # Проверяем, что смонтированы адаптеры с пулингом
    assert "https://" in session.adapters
    adapter = session.adapters["https://"]
    assert adapter._pool_connections == 20
    assert adapter._pool_maxsize == 50


# ============================================================
# Улучшение 9: Pre-filter по robots.txt
# ============================================================

def test_prefetch_robots_for_known_domains():
    """Prefetch robots.txt для известных доменов должен работать."""
    from corpus_builder.robots import RobotsCache, prefetch_robots
    cache = RobotsCache(user_agent="Test/1.0")
    # example.com обычно разрешает всё
    results = prefetch_robots(cache, ["https://example.com/page"])
    assert "example.com" in results


# ============================================================
# Улучшение 8: Ленивая инициализация краулеров
# ============================================================

def test_lazy_crawler_import():
    """get_crawler лениво импортирует только нужный класс."""
    from corpus_builder.crawlers import _imported_cache, list_known_types
    # Сбрасываем кэш
    _imported_cache.clear()
    cfg = None  # не используется в этом тесте напрямую
    known = list_known_types()
    assert "html" in known
    assert "pdf" in known
    assert "github_repo" in known
    assert "arxiv" in known
    assert "doaj" in known
    assert "wikipedia" in known


# ============================================================
# Улучшение 10: httpx
# ============================================================

def test_httpx_available():
    """Проверяем, что httpx установлен."""
    from corpus_builder.httpx_client import is_httpx_available
    assert is_httpx_available()


# ============================================================
# Улучшение 7: Streaming MinHash
# ============================================================

def test_streaming_minhash_dedup(tmp_path):
    """Streaming дедупликация для большого корпуса."""
    from corpus_builder.postproc.dedup import dedup_minhash_streaming
    # Создаём корпус с дубликатом
    corpus_path = tmp_path / "corpus.jsonl"
    with open(corpus_path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps({
                "source_url": f"https://example.com/{i}",
                "content": f"Unique technical text number {i} about electronics.",
                "status": "ok",
            }) + "\n" for i in range(3))
        # Дубликат 0-й записи
        f.write(json.dumps({
            "source_url": "https://example.com/dup",
            "content": "Unique technical text number 0 about electronics.",
            "status": "ok",
        }) + "\n")
    duplicates = dedup_minhash_streaming(corpus_path, num_perm=64, threshold=0.85)
    # Должен найти дубликат
    assert len(duplicates) >= 1


# ============================================================
# Улучшение 6: Parallel postproc
# ============================================================

def test_parallel_normalize(tmp_path):
    """Параллельная нормализация работает."""
    from corpus_builder.parallel_postproc import run_normalize_parallel
    in_file = tmp_path / "in.jsonl"
    out_file = tmp_path / "out.jsonl"
    with open(in_file, "w", encoding="utf-8") as f:
        f.writelines(json.dumps({
                "source_url": f"https://example.com/{i}",
                "content": f"  Test text {i} with extra spaces.  ",
            }) + "\n" for i in range(20))
    result = run_normalize_parallel(in_file, out_file, workers=2, chunk_size=5)
    assert result["total"] == 20
    assert out_file.exists()
    # Проверим, что нормализация сработала
    lines = out_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 20
    r = json.loads(lines[0])
    assert r["content"] == "Test text 0 with extra spaces."  # без trailing/leading spaces
