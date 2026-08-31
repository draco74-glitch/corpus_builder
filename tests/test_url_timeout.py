"""Тесты на per-URL timeout в pipeline."""
import time

import pytest


def test_crawl_with_timeout_completes():
    """Если crawl завершается быстро — возвращается результат."""
    from corpus_builder.pipeline import _crawl_with_timeout

    class FakeCrawler:
        def crawl(self, url, categories=None, source=None):
            class R:
                status = "ok"
                content = "test"
                source_url = url
            return R()

    crawler = FakeCrawler()
    result = _crawl_with_timeout(crawler, "https://example.com", [], timeout_seconds=10)
    assert result is not None
    assert result.status == "ok"


def test_crawl_with_timeout_raises_on_timeout():
    """Если crawl занимает слишком долго — поднимается _CrawlTimeoutError."""
    from corpus_builder.pipeline import _crawl_with_timeout, _CrawlTimeoutError

    class SlowCrawler:
        def crawl(self, url, categories=None, source=None):
            time.sleep(5)  # 5 секунд
            return None

    crawler = SlowCrawler()
    with pytest.raises(_CrawlTimeoutError):
        _crawl_with_timeout(crawler, "https://example.com", [], timeout_seconds=1)


def test_crawl_with_timeout_propagates_exception():
    """Если crawl поднимает исключение — оно пробрасывается."""
    from corpus_builder.pipeline import _crawl_with_timeout

    class CrashingCrawler:
        def crawl(self, url, categories=None, source=None):
            raise ValueError("Test crash")

    crawler = CrashingCrawler()
    with pytest.raises(ValueError, match="Test crash"):
        _crawl_with_timeout(crawler, "https://example.com", [], timeout_seconds=10)


def test_crawl_timeout_error_message():
    """_CrawlTimeoutError содержит URL в сообщении."""
    from corpus_builder.pipeline import _CrawlTimeoutError

    err = _CrawlTimeoutError("Timeout after 600s on https://example.com")
    assert "https://example.com" in str(err)
    assert "600" in str(err)


def test_pipeline_config_has_timeout():
    """PipelineConfig содержит per_url_timeout_minutes."""
    from corpus_builder.models import PipelineConfig

    cfg = PipelineConfig()
    assert hasattr(cfg, "per_url_timeout_minutes")
    assert cfg.per_url_timeout_minutes == 10  # default


def test_settings_have_the_same_timeout_field_as_the_engine():
    """Настройка таймаута — путь AppConfig: отдельного списка полей больше нет (В3)."""
    from corpus_builder.app_settings import AppSettings
    from corpus_builder.models import AppConfig, PipelineConfig, SourceItem

    s = AppSettings()
    assert s.get("pipeline.per_url_timeout_minutes") == PipelineConfig().per_url_timeout_minutes
    s.set("pipeline.per_url_timeout_minutes", 0.25)
    cfg = AppConfig(sources=[SourceItem(url="http://x", type="html")],
                    output={"corpus_file": "a.jsonl", "download_dir": "d"})
    assert s.apply_to_config(cfg) == ["pipeline.per_url_timeout_minutes"]
    assert cfg.pipeline.per_url_timeout_minutes == 0.25
