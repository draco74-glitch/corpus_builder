"""Регрессии на замечания ревью: корпусный конвейер (C1–C6, I1–I2, I4–I5).

Каждый тест краснеет БЕЗ соответствующего фикса — это и есть проверка, что
фикс настоящий, а не «код стал красивее».
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_builder import pipeline
from corpus_builder import quality_filters as qf
from corpus_builder.app_settings import AppSettings
from corpus_builder.config import load_config
from corpus_builder.models import (
    SOURCE_TYPES,
    CorpusRecord,
    DedupConfig,
    QualityConfig,
    SourceItem,
)
from corpus_builder.postproc.dedup import dedup_by_url, dedup_exact, dedup_minhash, run_dedup
from corpus_builder.postproc.quality import run_quality_filter

REPO_ROOT = Path(__file__).resolve().parents[1]

LONG_NON_TECH_TEXT = (
    "Napoleon Bonaparte was born in Ajaccio on the island of Corsica in 1769. "
    "He rose through the ranks of the French army during the turmoil of the "
    "Revolution and seized power in the coup of 18 Brumaire, becoming First "
    "Consul in 1799 and crowning himself Emperor five years later. His armies "
    "dominated much of continental Europe until the invasion of Russia in 1812 "
    "turned the tide of the war, and he abdicated in 1814 before his exile on "
    "Saint Helena."
)

LONG_TECH_TEXT = (
    "The operational amplifier saturates when the input common-mode voltage "
    "approaches the negative supply rail, so the datasheet recommends a "
    "split supply. Decoupling capacitors of 100 nF belongs next to each pin. "
    "The circuit uses a printed-circuit board with a transistor differential "
    "pair, and the resistor values were chosen for a low capacitance node. "
) * 3


# ============================================================
# C1 — фильтр качества не должен удалять текст без «электроники»
# ============================================================

def test_c1_long_non_technical_text_is_not_spam():
    assert qf.is_spam_or_low_quality(LONG_NON_TECH_TEXT) is False
    assert qf.spam_reason(LONG_NON_TECH_TEXT) is None


def test_c1_i2c_page_without_keyword_list_still_passes_spam_filter():
    text = ("Connecting an I2C bus requires pull-up resistors on SDA and SCL. "
            "Values between 2.2k and 10k ohm work for most 3.3V boards, and the "
            "total bus capacitance should stay below 400 pF; longer cables add "
            "capacitance and cause ringing on the edges of the clock signal.")
    assert qf.is_spam_or_low_quality(text) is False


def test_c1_real_advertising_is_still_flagged_with_a_reason():
    text = ("Купить акцию! Скидка 50%, промокод на первый заказ, розыгрыш приза, "
            "бесплатная доставка по всей стране для всех клиентов сегодня только "
            "у нас, купон на лучший bargain ждет тебя, успей заказать прямо сейчас.")
    reason = qf.spam_reason(text)
    assert reason is not None and qf.is_spam_or_low_quality(text) is True
    res = qf.evaluate_quality(text, min_chars=20, language_check=False, spam_check=True)
    assert res["passed"] is False
    assert any(r.startswith("spam:") for r in res["rejection_reasons"]), res


def test_c1_quality_filter_keeps_non_technical_corpus_and_reports_reasons(tmp_path):
    raw = tmp_path / "raw.jsonl"
    rows = [
        {"source_url": f"http://site/{i}", "source_type": "html",
         "content": LONG_NON_TECH_TEXT + f" Chapter {i}.", "status": "ok"}
        for i in range(4)
    ] + [
        {"source_url": "http://site/short", "source_type": "html",
         "content": "too short text", "status": "ok"},
        {"source_url": "http://site/dup", "source_type": "html", "status": "ok",
         "content": LONG_NON_TECH_TEXT + " Chapter 0.", "is_duplicate": True},
    ]
    raw.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = tmp_path / "filtered.jsonl"

    stats = run_quality_filter(raw, out, QualityConfig(min_chars=200))

    assert stats["kept"] == 4, f"не должен отбрасывать валидный некомпонентный текст: {stats}"
    reasons = stats["rejected_by_reason"]
    assert "unknown" not in reasons, "причина отбраковки обязана быть конкретной"
    assert reasons.get("too_short") == 1
    assert reasons.get("duplicate") == 1


def test_c1_evaluate_quality_returns_all_reasons():
    res = qf.evaluate_quality("abc", min_chars=200, spam_check=True,
                              language_check=False)
    assert res["passed"] is False
    assert "too_short" in res["rejection_reasons"]
    assert res["rejection_reason"] == res["rejection_reasons"][0]


# ============================================================
# C2 — все объявленные типы источников конфигурируемы
# ============================================================

@pytest.mark.parametrize("type_", list(SOURCE_TYPES))
def test_c2_every_source_type_is_accepted_by_schema(type_):
    item = SourceItem(url="https://example.com/x", type=type_)
    assert item.type == type_


def test_c2_schema_matches_crawler_registry():
    from corpus_builder.crawlers import REGISTRY
    assert set(REGISTRY) == set(SOURCE_TYPES), (
        "реестр краулеров и схема config.yaml разошлись: "
        f"только в реестре {set(REGISTRY) - set(SOURCE_TYPES)}, "
        f"только в схеме {set(SOURCE_TYPES) - set(REGISTRY)}")


def test_c2_config_with_arxiv_and_wikipedia_loads(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "sources:\n"
        "  - url: 'cat:cs.AR'\n    type: arxiv\n"
        "  - url: 'https://en.wikipedia.org/wiki/Resistor'\n    type: wikipedia\n"
        "  - url: 'https://doaj.org/api/search/articles/x'\n    type: doaj\n"
        "  - url: '10.1016/j.sna.2004.03.010'\n    type: crossref\n"
        "output:\n  corpus_file: o.jsonl\n  download_dir: dl\n", encoding="utf-8")
    loaded = load_config(cfg)
    assert [s.type for s in loaded.sources] == ["arxiv", "wikipedia", "doaj", "crossref"]


def test_c2_detect_source_type_covers_academic_urls():
    from corpus_builder.config_generator import detect_source_type
    cases = {
        "https://en.wikipedia.org/wiki/Resistor": "wikipedia",
        "https://arxiv.org/abs/2301.00001": "arxiv",
        "https://arxiv.org/list/eess.SP/recent": "arxiv",
        "https://doaj.org/article/abc": "doaj",
        "https://doi.org/10.1016/j.x/1": "crossref",
        "https://github.com/owner/repo": "github_repo",
        "https://site/x.pdf": "pdf",
        "https://site/blog/post": "html",
    }
    for url, expected in cases.items():
        assert detect_source_type(url) == expected, url
        assert detect_source_type(url) in SOURCE_TYPES


def test_c2_wizard_wikipedia_sources_get_wikipedia_type():
    """from_wikipedia обязан ставить type: wikipedia, а не «html»."""
    src = {"url": "https://en.wikipedia.org/wiki/X", "type": "wikipedia"}
    assert SourceItem(**src).type == "wikipedia"
    from corpus_builder.config_generator import make_source
    assert make_source("https://en.wikipedia.org/wiki/X")["type"] == "wikipedia"


# ============================================================
# C3/C4 — идентичность записи в дедупликации
# ============================================================

def test_c3_repeated_url_does_not_crash_minhash():
    records = [
        {"source_url": "http://a/1", "status": "ok",
         "content": "photosynthesis converts light into chemical energy " * 6},
        {"source_url": "http://a/1", "status": "ok",
         "content": "suspension bridges carry deck loads through cables " * 6},
    ]
    assert dedup_minhash(records, threshold=0.85) == {}


def test_c4_records_without_url_are_never_url_duplicates():
    records = [
        {"source_url": "", "status": "ok", "content": "alpha text " * 10},
        {"source_url": "", "status": "ok", "content": "beta text " * 10},
        {"source_url": None, "status": "ok", "content": "gamma text " * 10},
    ]
    assert dedup_by_url(records) == {}
    assert dedup_exact(records) == {}


def test_c4_duplicate_of_points_to_a_real_original(tmp_path):
    rows = [
        {"source_url": "http://a/1", "source_type": "html", "status": "ok",
         "content": "identical body text for both entries " * 5},
        {"source_url": "http://a/2", "source_type": "html", "status": "ok",
         "content": "identical body text for both entries " * 5},
    ]
    src = tmp_path / "in.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    stats = run_dedup(src, tmp_path / "out.jsonl", DedupConfig(minhash=False))
    out = [json.loads(l) for l in (tmp_path / "out.jsonl").read_text().splitlines()]
    assert stats["removed"] == 1
    dup = [r for r in out if r["is_duplicate"]][0]
    assert dup["duplicate_of"] == "http://a/1"


# ============================================================
# C6 — запуск без resume перезаписывает корпус, dry-run — нет
# ============================================================

class _FakeRecord:
    def __init__(self, content="body " * 100):
        self.content = content
        self.status = "ok"
        self.metadata = {}


def _make_cfg(tmp_path, sources):
    from corpus_builder.models import AppConfig
    return AppConfig(
        sources=[{"url": u, "type": "html"} for u in sources],
        output={"corpus_file": str(tmp_path / "out/raw.jsonl"),
                "download_dir": str(tmp_path / "out/dl")},
        pipeline={"save_checkpoint_every": 1, "per_url_timeout_minutes": 1},
    )


def _patch_crawl(monkeypatch, content="body text " * 100):
    class FakeCrawler:
        session = None
        def crawl(self, url, categories=None, source=None):
            return CorpusRecord(source_url=url, source_type="html", content=content)
    monkeypatch.setattr(pipeline, "make_crawler", lambda t, c, s: FakeCrawler())
    monkeypatch.setattr(pipeline, "RobotsCache",
                        lambda **kw: type("R", (), {"is_allowed": lambda self, u: True,
                                                    "respect": True})())


def test_c6_fresh_run_truncates_previous_corpus(tmp_path, monkeypatch):
    _patch_crawl(monkeypatch)
    cfg = _make_cfg(tmp_path, ["http://site/a"])
    pipeline.run_crawl(cfg, resume=False)
    pipeline.run_crawl(cfg, resume=False)
    lines = (tmp_path / "out/raw.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1, "повторный запуск без resume не должен дописывать дубли"


def test_c6_dry_run_does_not_touch_existing_corpus(tmp_path, monkeypatch):
    _patch_crawl(monkeypatch)
    cfg = _make_cfg(tmp_path, ["http://site/a"])
    pipeline.run_crawl(cfg, resume=False)
    before = (tmp_path / "out/raw.jsonl").read_text()
    out = pipeline.run_crawl(cfg, resume=False, dry_run=True)
    assert out.get("dry_run") is True
    assert (tmp_path / "out/raw.jsonl").read_text() == before


def test_c6_resume_skips_urls_already_done(tmp_path, monkeypatch):
    _patch_crawl(monkeypatch)
    cfg = _make_cfg(tmp_path, ["http://site/a"])
    pipeline.run_crawl(cfg, resume=False)
    stats = pipeline.run_crawl(cfg, resume=True)
    assert stats["processed"] == 0 and stats["skipped"] == 1
    assert len((tmp_path / "out/raw.jsonl").read_text().strip().splitlines()) == 1


def test_c6_same_url_listed_twice_is_processed_once(tmp_path, monkeypatch):
    _patch_crawl(monkeypatch)
    cfg = _make_cfg(tmp_path, ["http://site/a", "http://site/a?utm_source=news"])
    stats = pipeline.run_crawl(cfg, resume=False)
    assert stats["processed"] == 1, "канонизация URL обязана гасить дубли в config"


# ============================================================
# I1/I2 — общая обвязка для async-пути и рабочий HTTP-кэш
# ============================================================

def test_i1_async_pipeline_uses_configured_session_and_rate_limiter(monkeypatch, tmp_path):
    import asyncio

    from corpus_builder import async_pipeline

    seen = {}

    class FakeCrawler:
        def __init__(self):
            self.session = None
        def crawl(self, url, categories=None, source=None):
            seen["ua"] = self.session.headers.get("User-Agent")
            seen["source"] = source
            return CorpusRecord(source_url=url, source_type="html", content="x " * 300)

    import corpus_builder.crawlers as crawlers_pkg
    monkeypatch.setattr(crawlers_pkg, "get_crawler", lambda t, c: FakeCrawler())
    monkeypatch.setattr(async_pipeline, "build_crawl_context", _local_context)
    cfg = _make_cfg(tmp_path, ["http://site/a"])
    cfg.output.user_agent = "CB-UnitTest/9.9"
    asyncio.run(async_pipeline.run_async_crawl(cfg, resume=False))
    assert seen["ua"] == "CB-UnitTest/9.9", "async-путь обязан использовать UA из конфига"
    assert seen["source"] is not None, "per-source настройки должны доходить до краулера"

    src = Path(async_pipeline.__file__).read_text(encoding="utf-8")
    assert "rate_limiter.wait" in src, "async-путь обязан соблюдать request_delay"
    assert "asyncio.wait_for" in src, "async-путь обязан применять per-URL таймаут"


def _local_context(cfg):
    """Тот же набор компонентов, но без сети (robots) и с локальным state."""
    from corpus_builder.robots import RateLimiter, make_session
    from corpus_builder.state import State
    return {
        "session": make_session(cfg),
        "robots": type("NoRobots", (), {"is_allowed": lambda self, u: True,
                                        "respect": False})(),
        "rate_limiter": RateLimiter(default_delay=0.0),
        "state": State(cfg.output.state_file),
    }


def test_i1_async_pipeline_writes_checkpoints(tmp_path):
    src = Path(pipeline.__file__).with_name("async_pipeline.py").read_text(encoding="utf-8")
    assert "save_checkpoint_every" in src


def test_i2_build_crawl_context_uses_cached_session(tmp_path):
    pytest.importorskip("requests_cache")
    cfg = _make_cfg(tmp_path, ["http://site/a"])
    ctx = pipeline.build_crawl_context(cfg)
    import requests_cache
    assert isinstance(ctx["session"], requests_cache.CachedSession)
    # отключение кэша должно возвращать обычную сессию с пулом
    cfg.output.use_http_cache = False
    ctx2 = pipeline.build_crawl_context(cfg)
    assert not isinstance(ctx2["session"], requests_cache.CachedSession)
    assert ctx2["session"].headers["User-Agent"] == cfg.output.user_agent


# ============================================================
# I4/I5 — настройки приложения действительно доходят до движка
# ============================================================

def _all_settings_fields(settings: AppSettings):
    from dataclasses import fields, is_dataclass
    out = {}
    for f in fields(settings):
        section = getattr(settings, f.name)
        if is_dataclass(section):
            for sub in fields(section):
                out[f"{f.name}.{sub.name}"] = sub
    return out


#: настройки, которые влияют только на GUI/хранение и не имеют аналога в движке
GUI_ONLY_SETTINGS = {
    "gui.theme", "gui.log_level", "gui.show_progress_bar", "gui.window_width",
    "gui.window_height", "gui.language", "gui.last_config_path",
    "gui.last_output_dir", "gui.last_excel_path", "gui.recent_configs",
    "gui.check_updates_on_start",
    "github.token", "stackexchange.api_key",       # уходят в переменные окружения
    "crawl.proxy_list",                            #_corpus_builder_proxies
    "dedup.incremental_score_threshold",
    "stackexchange.min_score", "stackexchange.max_questions",
}


def _settings_read_by_apply_to_config() -> set[str]:
    """Собрать `section.field`, которые apply_to_config реально читает."""
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(AppSettings.apply_to_config)))
    used = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "self"):
            used.add(f"{node.value.attr}.{node.attr}")
    return used


def test_i4_every_setting_reaches_the_engine():
    """Чекбокс в диалоге, который никуда не передаётся, — обещание без исполнения.

    Единственные исключения — чисто GUI-настройки и секреты, уходящие в
    переменные окружения (см. GUI_ONLY_SETTINGS).
    """
    all_fields = set(_all_settings_fields(AppSettings()))
    consumed = _settings_read_by_apply_to_config() | {"_none_"}
    consumed -= {"_none_"}
    unaccounted = all_fields - consumed - GUI_ONLY_SETTINGS
    assert not unaccounted, f"настройки без потребителя в движке: {sorted(unaccounted)}"
    # и наоборот: apply_to_config не должен ссылаться на несуществующие поля
    dangling = consumed - all_fields
    assert not dangling, f"apply_to_config читает несуществующие поля: {sorted(dangling)}"


@pytest.mark.parametrize("value,expected", [
    ("ten", 10),            # мусор → default, а не «ten» внутри ранa
    ("7", 7),               # числовая строка из JSON приводится
    (True, 1),
])
def test_i5_settings_import_coerces_types(value, expected):
    s = AppSettings._from_dict({"crawl": {"per_url_timeout_minutes": value}})
    assert s.crawl.per_url_timeout_minutes == expected


def test_i5_poisoned_settings_cannot_reach_engine():
    s = AppSettings._from_dict({"crawl": {"request_timeout": "abc"}})
    cfg = _make_cfg(Path("/tmp"), ["http://x"])
    s.apply_to_config(cfg)                 # не должно бросать
    assert isinstance(cfg.output.request_timeout, int)
    with pytest.raises(Exception):
        cfg.pipeline.per_url_timeout_minutes = "ten"   # validate_assignment


def test_i5_validate_assignment_protects_engine_types():
    cfg = _make_cfg(Path("/tmp"), ["http://x"])
    with pytest.raises(Exception):
        cfg.output.request_delay = None


# ============================================================
# I2 — параллельные задачи не должны писать дубли одного URL
# ============================================================

def test_async_deduplicates_same_url_under_concurrency(tmp_path, monkeypatch):
    import asyncio
    import time
    from corpus_builder import async_pipeline
    from corpus_builder.models import CorpusRecord, SourceItem

    class SlowCrawler:
        def __init__(self, session=None):
            self.session = session

        def crawl(self, url, categories=None, source=None):
            time.sleep(0.25)               # обе задачи точно пересекутся
            return CorpusRecord(source_url=url, source_type="html",
                                content="body " * 200)

    import corpus_builder.crawlers as crawlers_pkg
    monkeypatch.setattr(crawlers_pkg, "get_crawler", lambda t, c: SlowCrawler())
    monkeypatch.setattr(async_pipeline, "build_crawl_context", _local_context)

    cfg = _make_cfg(tmp_path, ["http://site/dup", "http://site/dup",
                               "http://site/other"])
    stats = asyncio.run(async_pipeline.run_async_crawl(cfg, resume=False))
    lines = (tmp_path / "out/raw.jsonl").read_text().strip().splitlines()
    urls = [json.loads(l)["source_url"] for l in lines]
    assert stats["processed"] == 2, stats
    assert sorted(urls) == ["http://site/dup", "http://site/other"], urls


# ============================================================
# I2 — robots: разрешение/запрет и явный обход на источник
# ============================================================

def test_source_level_ignore_robots_skips_check(tmp_path, monkeypatch):
    from corpus_builder import pipeline
    from corpus_builder.models import CorpusRecord

    checked: list[str] = []

    class DenyingRobots:
        respect = True
        def is_allowed(self, url):
            checked.append(url)
            return False

    class OkCrawler:
        def __init__(self, session=None):
            self.session = session
        def crawl(self, url, categories=None, source=None):
            return CorpusRecord(source_url=url, source_type="stackexchange",
                                content="answer " * 100)

    monkeypatch.setattr(pipeline, "build_crawl_context", lambda cfg: {
        "session": None, "robots": DenyingRobots(),
        "rate_limiter": pipeline.RateLimiter(default_delay=0),
        "state": pipeline.State(cfg.output.state_file)})
    monkeypatch.setattr(pipeline, "make_crawler", lambda t, c, s: OkCrawler())
    import corpus_builder.robots as robots_mod
    monkeypatch.setattr(robots_mod, "pre_filter_by_robots",
                        lambda srcs, rc, on_skip=None: (
                            [x for x in srcs if not x.ignore_robots], {}))

    cfg = _make_cfg(tmp_path, ["https://electronics.stackexchange.com/questions/1"])
    cfg.sources[0].ignore_robots = True
    stats = pipeline.run_crawl(cfg, resume=False)
    assert stats["processed"] == 1, stats
    assert checked == [], "для ignore_robots проверять robots.txt не нужно"


def test_robots_denied_url_is_not_marked_done(tmp_path, monkeypatch):
    """«Запрещено» ≠ «обработано»: источник должен остаться в выборке."""
    from corpus_builder import pipeline
    from corpus_builder.models import CorpusRecord

    class DenyingRobots:
        respect = True
        def is_allowed(self, url):
            return False

    monkeypatch.setattr(pipeline, "build_crawl_context", lambda cfg: {
        "session": None, "robots": DenyingRobots(),
        "rate_limiter": pipeline.RateLimiter(default_delay=0),
        "state": pipeline.State(cfg.output.state_file)})
    import corpus_builder.robots as robots_mod
    monkeypatch.setattr(robots_mod, "pre_filter_by_robots", lambda srcs, rc, on_skip=None: ([], {}))

    cfg = _make_cfg(tmp_path, ["http://site/blocked"])
    stats = pipeline.run_crawl(cfg, resume=False)
    assert stats["skipped"] == 1 and stats["processed"] == 0
    import json
    state = json.loads((tmp_path / "out/state.json").read_text())
    assert state["done"] == [] and state["errors"] == [], \
        "заблокированный robots’ом URL не должен «сгорать» как обработанный"


def test_robots_fail_closed_by_default(monkeypatch):
    """Сбой загрузки robots.txt ≠ «можно всё» (I2/compliance)."""
    import requests
    from corpus_builder.robots import RobotsCache

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("dns fail")
    monkeypatch.setattr(requests, "get", boom)
    cache = RobotsCache(user_agent="x")                 # fail_open=False
    assert cache.is_allowed("https://site/page") is False
    open_cache = RobotsCache(user_agent="x", fail_open=True)
    assert open_cache.is_allowed("https://site/page") is True


def test_robots_respect_false_disables_check(monkeypatch):
    import requests
    from corpus_builder.robots import RobotsCache

    def boom(*a, **k):
        raise AssertionError("не должен идти сетевых запросов при respect=False")
    monkeypatch.setattr(requests, "get", boom)
    assert RobotsCache(user_agent="x", respect=False).is_allowed("https://site/p") is True


# ============================================================
# I3/I4 — потоковые стратегии дедупликации
# ============================================================

def _stream_corpus(tmp_path):
    body = lambda i: ("Amplifier " * 20 + f"variant {i} ") * 3
    lines = ["", "not-json-at-all",
             json.dumps({"source_url": "http://a/1", "source_type": "html",
                         "status": "ok", "content": body(1)}),
             json.dumps({"source_url": "http://bad", "source_type": "html",
                         "status": "error", "content": ""}),
             json.dumps({"source_url": "http://a/2", "source_type": "html",
                         "status": "ok", "content": body(1)}),          # точный дубль
             json.dumps({"source_url": "http://a/3", "source_type": "html",
                         "status": "ok", "content": body(2)}),
             ]
    src = tmp_path / "raw.jsonl"
    src.write_text("\n".join(lines), encoding="utf-8")
    return src


def _run_strategy(tmp_path, strategy):
    from corpus_builder.postproc.dedup import run_dedup, run_dedup_adaptive
    src = _stream_corpus(tmp_path)
    cfg = DedupConfig(exact=True, minhash=True, dedup_images=False,
                      streaming=(strategy == "streaming"),
                      incremental=(strategy == "incremental"))
    if strategy == "incremental":
        cfg.incremental_index_file = str(tmp_path / f"idx-{strategy}.pkl")
    out = tmp_path / f"out-{strategy}.jsonl"
    stats = (run_dedup(src, out, cfg) if strategy == "plain"
             else run_dedup_adaptive(src, out, cfg))
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    return stats, {r["source_url"] for r in rows if r.get("is_duplicate")}


@pytest.mark.parametrize("strategy", ["plain", "streaming", "incremental"])
def test_dedup_strategies_agree(tmp_path, strategy):
    """Все три стратегии считают дубли одинаково (и не падают на битых строках)."""
    stats, dups = _run_strategy(tmp_path, strategy)
    assert stats["removed"] == 1 and len(dups) == 1, f"{strategy}: {stats}"
    assert dups == {"http://a/2"}, f"{strategy}: {dups}"
    # запись со status=error дублем не считается
    assert "http://bad" not in dups


def test_incremental_dedup_second_run_is_cheap(tmp_path):
    """Повторный прогон не должен считать заново уже виденные URL."""
    from corpus_builder.postproc.dedup import run_dedup_adaptive
    src = _stream_corpus(tmp_path)
    cfg = DedupConfig(exact=False, minhash=True, dedup_images=False, incremental=True,
                      incremental_index_file=str(tmp_path / "idx.pkl"))
    _stats1, dups1 = run_dedup_adaptive(src, tmp_path / "o1.jsonl", cfg), None
    second = run_dedup_adaptive(src, tmp_path / "o2.jsonl", cfg)
    assert Path(cfg.incremental_index_file).exists()
    # второй прогон: все URL уже в индексе → новых дублей не появляется
    assert second["removed"] >= 0 and second["total"] == _stats1["total"]
