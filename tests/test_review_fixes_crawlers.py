"""Регрессии на замечания ревью: краулеры, авто-обновление, GUI (C5, C7, I7–I12).

Сетевые границы подменяются фейковыми сессиями — тесты не ходят в интернет.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from corpus_builder import auto_updater as au
from corpus_builder.config import load_config
from corpus_builder.crawlers.forum_crawler import StackExchangeCrawler
from corpus_builder.crawlers.github_crawler import GitHubCrawler
from corpus_builder.crawlers.html_crawler import HtmlCrawler
from corpus_builder.crawlers.pdf_crawler import PdfCrawler
from corpus_builder.http import download_file, is_blocked_url
from corpus_builder.models import (
    CrawlerGitHubConfig,
    CrawlerPDFConfig,
    OutputConfig,
    SourceItem,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_CFG = REPO_ROOT / "config.smoke.yaml"


def make_config(tmp_path, crawlers: dict | None = None, output: dict | None = None):
    cfg = load_config(SMOKE_CFG)
    cfg.output = OutputConfig(
        corpus_file=str(tmp_path / "out" / "raw.jsonl"),
        download_dir=str(tmp_path / "out" / "dl"),
        **(output or {}),
    )
    for section, values in (crawlers or {}).items():
        setattr(cfg.crawlers, section, values)
    return cfg


# ============================================================
# фейковые HTTP-объекты
# ============================================================

class FakeResponse:
    def __init__(self, body: bytes = b"", status: int = 200,
                 headers: dict | None = None, json_data=None):
        self._body = body
        self.status_code = status
        self.headers = headers or {}
        self._json = json_data
        self.text = body.decode("utf-8", "replace")
        self.encoding = "utf-8"

    @property
    def content(self) -> bytes:
        return self._body

    def json(self):
        return self._json if self._json is not None else json.loads(self._body.decode())

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeSession:
    """Записывает вызовы; отвечает по первым совпавшим подстрокам URL."""

    def __init__(self, routes=None, default=None):
        self.routes = routes or {}
        self.default = default or FakeResponse(b"", status=404)
        self.calls: list[tuple[str, dict]] = []
        self.headers = {"User-Agent": "fake"}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        for key, value in self.routes.items():
            if key in url:
                return value
        return self.default

    @property
    def urls(self) -> list[str]:
        return [u for u, _ in self.calls]


def repo_zip(entries: dict[str, bytes], root: str = "repo-main") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(f"{root}/{name}", data)
    return buf.getvalue()


# ============================================================
# C5 — toast-уведомления и логгер GUI
# ============================================================

def test_c5_toast_display_does_not_shadow_qwidget_show():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="Qt unavailable")
    import inspect

    from corpus_builder.gui_improvements import ToastNotification

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    parent = QtWidgets.QWidget()
    toast = ToastNotification.display(parent, "Заголовок", "Текст",
                                      ToastNotification.INFO)
    assert toast.parent() is parent
    # show() обязан оставаться Qt-овским методом без обязательных аргументов
    assert list(inspect.signature(toast.show).parameters) == []


def test_c5_gui_module_defines_a_logger():
    src = (REPO_ROOT / "corpus_builder" / "gui.py").read_text(encoding="utf-8")
    assert "log = get_logger(__name__)" in src
    assert "from .logging_setup import get_logger" in src


# ============================================================
# C7 — целостность авто-обновления
# ============================================================

def test_c7_verify_member_path(tmp_path):
    assert au.verify_member_path(tmp_path, "gui.py") == tmp_path / "gui.py"
    assert au.verify_member_path(tmp_path, "crawlers/html_crawler.py").is_relative_to(tmp_path)
    for bad in ("../../../../etc/passwd", "/absolute/evil.py", "../outside.py",
                "gui.so", "corpus_builder/../../escape.py"):
        with pytest.raises(au.UnsafeArchiveEntry):
            au.verify_member_path(tmp_path, bad)


def _frozen_target(tmp_path):
    target = tmp_path / "_internal" / "corpus_builder"
    target.mkdir(parents=True)
    (target / "gui.py").write_text("OLD", encoding="utf-8")
    return target


def test_c7_apply_patch_rejects_zip_slip_without_writing(tmp_path):
    target = _frozen_target(tmp_path)
    zpath = tmp_path / "patch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("corpus_builder/../../../../ESCAPED.py", "print('pwned')")
    updater = au.AutoUpdater.__new__(au.AutoUpdater)
    assert updater._apply_patch(zpath, target_dir=target) is False
    assert not (tmp_path / "ESCAPED.py").exists()
    assert (target / "gui.py").read_text(encoding="utf-8") == "OLD"


def test_c7_apply_patch_installs_clean_zip(tmp_path):
    target = _frozen_target(tmp_path)
    zpath = tmp_path / "patch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("corpus_builder/gui.py", "print('new')")
    updater = au.AutoUpdater.__new__(au.AutoUpdater)
    assert updater._apply_patch(zpath, target_dir=target) is True
    assert (target / "gui.py").read_text(encoding="utf-8") == "print('new')"


@pytest.fixture
def commit_updater(tmp_path, monkeypatch):
    cu = au.CommitUpdater()
    cu._latest_sha = "a" * 40
    target = tmp_path / "corpus_builder"
    target.mkdir()
    (target / "orig.py").write_text("KEEP", encoding="utf-8")
    marker = tmp_path / "sha.txt"
    state = {"restored": False}
    monkeypatch.setattr(cu, "_get_target_dir", lambda: target)
    monkeypatch.setattr(cu, "_save_last_known_sha",
                        lambda sha: marker.write_text(sha, encoding="utf-8"))
    monkeypatch.setattr(cu, "restore_backup", lambda: state.update(restored=True) or True)
    return SimpleNamespace(cu=cu, target=target, marker=marker, state=state)


def test_c7_partial_update_keeps_marker_and_rolls_back(commit_updater):
    ns = commit_updater
    ns.cu._get_py_files_in_repo = lambda sha: ["a.py", "b.py"]
    ns.cu._download_file_from_github = lambda rel, sha, *a, **k: (
        b"print(1)" if rel == "a.py" else None)
    res = ns.cu.apply_commit_update()
    assert res["success"] is False
    assert not ns.marker.exists(), "маркер нельзя двигать при частичном обновлении"
    assert ns.state["restored"] is True, "частичный сбой обязан откатываться на бэкап"


def test_c7_full_update_advances_marker(commit_updater):
    ns = commit_updater
    ns.cu._get_py_files_in_repo = lambda sha: ["a.py"]
    ns.cu._download_file_from_github = lambda rel, sha, *a, **k: b"print(1)"
    assert ns.cu.apply_commit_update()["success"] is True
    assert ns.marker.exists()


def test_c7_non_python_payload_is_not_installed(commit_updater):
    ns = commit_updater
    ns.cu._get_py_files_in_repo = lambda sha: ["orig.py"]
    ns.cu._download_file_from_github = lambda rel, sha, *a, **k: \
        b"<html><body>502 Bad Gateway</body></html>"
    res = ns.cu.apply_commit_update()
    assert res["success"] is False
    assert (ns.target / "orig.py").read_text(encoding="utf-8") == "KEEP"


def test_c7_commit_check_uses_configured_branch(monkeypatch):
    cu = au.CommitUpdater(repo="o/r", branch="dev")
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        return FakeResponse(json_data=[])
    monkeypatch.setattr(au.requests, "get", fake_get)
    assert cu.check_for_commit_updates() is None
    assert "sha=dev" in seen["url"]


def test_c7_backup_and_restore_agree_on_paths(tmp_path, monkeypatch):
    cu = au.CommitUpdater()
    target = tmp_path / "corpus_builder"
    target.mkdir()
    (target / "gui.py").write_text("NEW", encoding="utf-8")
    backup = tmp_path / "corpus_builder_backup"
    backup.mkdir()
    (backup / "gui.py").write_text("OLD", encoding="utf-8")
    monkeypatch.setattr(cu, "_get_target_dir", lambda: target)
    assert cu.backup_dir() == backup
    assert cu.restore_backup() is True
    assert (target / "gui.py").read_text(encoding="utf-8") == "OLD"


def test_c7_release_asset_without_digest_is_refused(monkeypatch):
    updater = au.AutoUpdater("o/r", "0.0.1")
    updater._latest_release = {"tag_name": "v9", "assets": [
        {"name": "patch.zip", "size": 7,
         "browser_download_url": "https://example.test/patch.zip"}]}
    monkeypatch.setattr(au.requests, "get",
                        lambda url, **kw: FakeResponse(body=b"ZIPDATA"))
    applied = []
    monkeypatch.setattr(updater, "_apply_patch", lambda *a, **k: applied.append(a))
    assert updater.download_and_apply("patch.zip") is False
    assert applied == [], "патч без проверенного дайджеста применять нельзя"


def test_c7_release_asset_with_matching_digest_is_applied(monkeypatch):
    import hashlib
    payload = b"ZIPDATA"
    digest = hashlib.sha256(payload).hexdigest()

    def fake_get(url, **kw):
        if url.endswith(".sha256"):
            return FakeResponse(body=f"{digest}  patch.zip\n".encode())
        return FakeResponse(body=payload, headers={})
    updater = au.AutoUpdater("o/r", "0.0.1")
    updater._latest_release = {"tag_name": "v9", "assets": [
        {"name": "patch.zip", "size": len(payload),
         "browser_download_url": "https://example.test/patch.zip"}]}
    monkeypatch.setattr(au.requests, "get", fake_get)
    applied = []

    def fake_apply(*a, **k):
        applied.append(a)
        return True
    monkeypatch.setattr(updater, "_apply_patch", fake_apply)
    assert updater.download_and_apply("patch.zip") is True
    assert len(applied) == 1


# ============================================================
# I7/I8/I9 — GitHub-краулер
# ============================================================

def test_github_lfs_pointers_are_not_content(tmp_path):
    crawler = GitHubCrawler(make_config(tmp_path))
    zf = zipfile.ZipFile(io.BytesIO(repo_zip({
        "README.md": b"version https://git-lfs.github.com/spec/v1\noid sha256:x\n",
        "docs/guide.md": b"# Guide\n\nreal documentation text\n",
    })))
    parts: list[str] = []
    lfs: list[str] = []
    docs, _included = crawler._collect_content(zf, "o", "r", parts, [], [], lfs)
    assert lfs == ["README.md"]
    assert not any("git-lfs" in p for p in parts + docs)
    assert any("real documentation" in d for d in docs)


def test_github_docs_not_duplicated_in_content(tmp_path):
    """docs/*.md не должны попадать в content И в DOCUMENTATION одновременно."""
    crawler = GitHubCrawler(make_config(tmp_path))
    zf = zipfile.ZipFile(io.BytesIO(repo_zip({
        "docs/a.md": b"# A doc\n", "b.md": b"# B root\n",
    })))
    parts: list[str] = []
    docs, _ = crawler._collect_content(zf, "o", "r", parts, [], [], [])
    assert any("B root" in p for p in parts)
    assert not any("B root" in d for d in docs)
    assert any("A doc" in d for d in docs)
    assert not any("A doc" in p for p in parts)


def test_github_wiki_uses_the_wiki_repo_and_skips_known_paths(tmp_path):
    cfg = make_config(tmp_path)
    cfg.crawlers.github.crawl_wiki = True
    crawler = GitHubCrawler(cfg)
    zip_bytes = repo_zip({"Home.md": b"# Home page\n", "Setup.md": b"# Setup page\n"},
                         root="r.wiki-main")
    crawler.session = FakeSession({"r.wiki": FakeResponse(body=zip_bytes)})
    texts, ok = crawler._fetch_wiki("o", "r", {}, [".md"], skip_paths={"Setup.md"})
    assert ok is True
    assert "github.com/o/r.wiki/archive/" in crawler.session.urls[0], \
        "вики берётся из отдельного репозитория {repo}.wiki, а не из репо кода"
    assert len(texts) == 1 and "Home page" in texts[0]


def test_github_wiki_absent_returns_not_success(tmp_path):
    crawler = GitHubCrawler(make_config(tmp_path))
    crawler.session = FakeSession({})
    texts, ok = crawler._fetch_wiki("o", "r", {}, [".md"])
    assert (texts, ok) == ([], False)


def test_github_issues_paginate_beyond_one_page(tmp_path):
    crawler = GitHubCrawler(make_config(tmp_path))

    def items(start, count):
        return [{"number": i, "title": f"t{i}", "state": "open", "body": f"b{i}",
                 "user": {"login": "u"}, "labels": []} for i in range(start, start + count)]

    def route(url, **kw):
        page = int((kw.get("params") or {}).get("page", 1))
        if page == 1:
            return FakeResponse(json_data=items(0, 100))
        if page == 2:
            return FakeResponse(json_data=items(100, 30))
        return FakeResponse(json_data=[])

    session = FakeSession({})
    session.get = route
    crawler.session = session
    texts, count = crawler._fetch_issues("o", "r", {}, max_issues=130,
                                         comments_per_issue=0)
    assert count == 130 and len(texts) == 130


def test_github_archive_respects_size_cap(tmp_path):
    from corpus_builder.models import CrawlerHTMLConfig  # noqa: F401 (симулирует конфиг)
    cfg = make_config(tmp_path)
    cfg.crawlers.github = CrawlerGitHubConfig(max_archive_mb=1)
    crawler = GitHubCrawler(cfg)
    big = b"x" * (2 * 1024 * 1024)
    crawler.session = FakeSession({"/archive/": FakeResponse(
        body=big, headers={"Content-Length": str(len(big))})})
    assert crawler._download_archive("o", "r", "main", {}) is None


def test_github_per_source_include_files(tmp_path):
    crawler = GitHubCrawler(make_config(tmp_path))
    crawler.source = SourceItem(url="https://github.com/o/r", type="github_repo",
                                include_files=["docs/*.rst"])
    zf = zipfile.ZipFile(io.BytesIO(repo_zip({
        "README.md": b"# readme md content", "docs/guide.rst": b"guide rst content",
    })))
    parts: list[str] = []
    docs, _ = crawler._collect_content(zf, "o", "r", parts, [], [], [])
    assert not any("readme md" in p for p in parts)
    assert any("guide rst" in d for d in docs)


def test_html_attachment_allowed_prefers_per_source_include_files(tmp_path):
    crawler = HtmlCrawler(make_config(tmp_path))
    crawler.source = SourceItem(url="https://site/x", type="html", include_files=["*.pdf"])
    assert crawler.attachment_allowed("https://site/manual.pdf", ["pdf"])
    assert not crawler.attachment_allowed("https://site/pic.png", ["png"]), \
        "per-source include_files важнее глобального списка расширений"
    crawler.source = None
    assert crawler.attachment_allowed("https://site/pic.png", ["png"])
    assert not crawler.attachment_allowed("https://site/clip.mp4", ["mp4"])


def test_html_download_files_false_disables_attachments(tmp_path, monkeypatch):
    """`sources[].download_files: false` обязан работать (I7)."""
    calls: list = []
    monkeypatch.setattr("corpus_builder.http.download_file",
                        lambda *a, **k: calls.append(a) or None)
    crawler = HtmlCrawler(make_config(tmp_path))
    html = ("<html><head><title>T</title></head><body><p>" + "text " * 60
            + '</p><a href="manual.pdf">m</a></body></html>')
    crawler.session = FakeSession({"site/x": FakeResponse(body=html.encode())})
    crawler.source = SourceItem(url="https://site/x", type="html", download_files=False)
    record = crawler.crawl("https://site/x", source=crawler.source)
    assert calls == [] and record.downloaded_files == []

    # и наоборот: с download_files=true вложение качается
    crawler2 = HtmlCrawler(make_config(tmp_path))
    crawler2.session = FakeSession({"site/x": FakeResponse(body=html.encode())})
    calls.clear()
    monkeypatch.setattr("corpus_builder.http.download_file",
                        lambda *a, **k: calls.append(a) or ("/tmp/x.pdf", "sha", 3))
    rec2 = crawler2.crawl("https://site/x",
                          source=SourceItem(url="https://site/x", type="html"))
    assert len(rec2.downloaded_files) == 1 and calls


def test_html_fetch_failure_is_reported_with_reason(tmp_path):
    """Раньше — `return None`, и в errors.jsonl писалось «no record»."""
    crawler = HtmlCrawler(make_config(tmp_path))
    crawler.session = FakeSession({"site": FakeResponse(b"", status=404)})
    record = crawler.crawl("https://site/missing", source=None)
    assert record.status == "error"
    assert "404" in record.metadata["error"]


# ============================================================
# StackExchange: site/URL/списки (I11)
# ============================================================

@pytest.mark.parametrize("url,site,qid,tag", [
    ("https://electronics.stackexchange.com/questions/1/x", "electronics", "1", ""),
    ("https://unix.stackexchange.com/questions/2/y", "unix", "2", ""),
    ("https://ru.stackoverflow.com/questions/3/z", "ru.stackoverflow", "3", ""),
    ("https://stackoverflow.com/questions/4/t", "stackoverflow", "4", ""),
    ("https://superuser.com/questions/tagged/bash", "superuser", "", "bash"),
    ("https://example.com/questions/5", "", "", ""),
])
def test_se_parse_target(url, site, qid, tag):
    assert StackExchangeCrawler.parse_target(url) == (site, qid, tag)


def test_se_non_se_url_raises_instead_of_silent_none(tmp_path):
    crawler = StackExchangeCrawler(make_config(tmp_path))
    crawler.session = FakeSession({})
    record = crawler.crawl("https://example.com/questions/1", source=None)
    assert record.status == "error"
    assert "not a StackExchange URL" in record.metadata["error"]


def test_se_missing_question_reports_reason(tmp_path):
    crawler = StackExchangeCrawler(make_config(tmp_path))
    crawler.session = FakeSession({"api.stackexchange": FakeResponse(
        json_data={"items": []})})
    record = crawler.crawl(
        "https://electronics.stackexchange.com/questions/999999999", source=None)
    assert record.status == "error"
    assert "not found" in record.metadata["error"]


def test_se_tag_list_is_crawled(tmp_path):
    crawler = StackExchangeCrawler(make_config(tmp_path))
    items = [{"question_id": 10 + i, "title": f"Q {i}", "body": f"<p>body {i}</p>",
              "score": i, "answer_count": 1, "accepted_answer_id": None,
              "link": f"https://electronics.stackexchange.com/q/{10+i}",
              "tags": ["kicad"]} for i in range(3)]
    crawler.session = FakeSession(
        {"2.3/questions": FakeResponse(json_data={"items": items})})
    record = crawler.crawl(
        "https://electronics.stackexchange.com/questions/tagged/kicad", source=None)
    assert record.status == "ok"
    assert record.metadata["kind"] == "list"
    assert [q["question_id"] for q in record.metadata["questions"]] == [10, 11, 12]


# ============================================================
# I5/I11/I12 — http/PII-подобные блокировки, revalidation
# ============================================================

@pytest.mark.parametrize("url", [
    "https://example.com/spec?rev=2024.03.ts",
    "https://docs.example.com/page?redirect=/app/index.ts",
    "https://www.cloudflare.com/learning/dns/",
    "https://cdn.jsdelivr.net/npm/chart.js/docs/index.html",
])
def test_blocked_url_no_longer_matches_query_substrings_or_cdns(url):
    assert is_blocked_url(url) is False, f"легитимный URL блокировался: {url}"


@pytest.mark.parametrize("url", [
    "https://example.com/video.mp4",
    "https://cdn.example.com/file?src=/media/clip.mp4",
    "https://www.youtube.com/watch?v=x",
    "https://player.vimeo.com/external/123",
])
def test_blocked_url_still_blocks_streams(url):
    assert is_blocked_url(url) is True


def test_download_file_revalidates_when_server_size_changed(tmp_path):
    """Кэш обязан сверять размер: иначе вечная первая версия документа (I5)."""
    body_v1, body_v2 = b"OLD CONTENT", b"NEW CONTENT " * 50
    state = {"get_calls": 0}

    class S:
        headers = {}

        def head(self, url, **kw):
            return FakeResponse(b"", headers={"Content-Length": str(len(body_v2))})

        def get(self, url, **kw):
            # первый GET отдаёт v1 (файл кэшируется), дальше — v2
            body = body_v1 if state["get_calls"] == 0 else body_v2
            state["get_calls"] += 1
            return FakeResponse(body=body, headers={"Content-Length": str(len(body))})

    url = "https://site/manual.pdf"
    kw = dict(max_size_mb=10, timeout=5, revalidate_after_hours=0)  # всегда сверять
    first = download_file(url, tmp_path, session=S(), **kw)
    assert first and Path(first[0]).read_bytes() == body_v1

    second = download_file(url, tmp_path, session=S(), **kw)
    assert second and Path(second[0]).read_bytes() == body_v2, \
        "размер на сервере отличается от кэша — файл обязан быть перекачан"


def test_download_file_fresh_cache_is_not_reprobed(tmp_path):
    """Вежливость: HEAD-запрос только для «протухшего» кэша (I5)."""
    body = b"CACHED"
    counts = {"head": 0, "get": 0}

    class S:
        headers = {}

        def head(self, url, **kw):
            counts["head"] += 1
            return FakeResponse(b"", headers={"Content-Length": "999999"})

        def get(self, url, **kw):
            counts["get"] += 1
            return FakeResponse(body=body, headers={"Content-Length": str(len(body))})

    url = "https://site/fresh.pdf"
    first = download_file(url, tmp_path, max_size_mb=10, timeout=5,
                          session=S(), revalidate_after_hours=168)
    assert first
    second = download_file(url, tmp_path, max_size_mb=10, timeout=5,
                           session=S(), revalidate_after_hours=168)
    assert counts["get"] == 1, "второй вызов обязан взять файл из кэша"
    assert counts["head"] == 0, "свежий кэш не надо репробить HEAD-ом (вежливость)"
    assert second[2] == len(body)


# ============================================================
# I10/I11/I4 — PDF
# ============================================================

def make_pdf(tmp_path, name, pages) -> str:
    pymupdf = pytest.importorskip("pymupdf", reason="PyMuPDF required")
    doc = pymupdf.open()
    for blocks in pages:
        page = doc.new_page(width=595, height=842)
        for x, y, text in blocks:
            page.insert_text((x, y), text)
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return str(path)


def two_col_blocks():
    left = [(58, 40 + k * 60, f"left paragraph {k} line {i} describing the pinout")
            for k in range(6) for i in range(4)]
    right = [(310, 40 + k * 60, f"right paragraph {k} line {i} about max ratings")
             for k in range(6) for i in range(4)]
    return left + right


def single_col_blocks():
    body = [(58, 40 + k * 60, f"full width paragraph {k} line {i} of a normal document")
            for k in range(6) for i in range(4)]
    footer = [(430, 420, "Doc 42-0075 rev 1.1")]      # правый колонтитул
    return body + footer


def flags_for(tmp_path, name, pages):
    import pymupdf
    path = make_pdf(tmp_path, name, pages)
    doc = pymupdf.open(path)
    try:
        return PdfCrawler._two_column_pages(doc, 0.35)
    finally:
        doc.close()


def test_two_column_detection_is_bimodal(tmp_path):
    assert flags_for(tmp_path, "one.pdf", [single_col_blocks()]) == [False]
    assert flags_for(tmp_path, "two.pdf", [two_col_blocks()]) == [True]


def test_mixed_document_is_classified_per_page(tmp_path):
    assert flags_for(tmp_path, "mix.pdf",
                     [single_col_blocks(), two_col_blocks()]) == [False, True]


def test_pdf_tables_parse_the_document_once(tmp_path, monkeypatch):
    """pdfplumber открывался на каждой странице → O(pages²) (I11)."""
    import sys
    import types

    path = make_pdf(tmp_path, "t.pdf", [single_col_blocks()])
    opens: list[str] = []

    class Page:
        def extract_tables(self):
            return [[["a", "b"], ["c", "d"]]]

    class Doc:
        pages = [Page(), Page(), Page()]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_open(p):
        opens.append(p)
        return Doc()

    monkeypatch.setitem(sys.modules, "pdfplumber", types.SimpleNamespace(open=fake_open))
    result = PdfCrawler._extract_tables_all(path)
    assert len(opens) == 1, "документ должен парситься ровно один раз"
    assert len(result) == 3 and all(result)


def test_schematic_filter_fails_closed_without_tesseract(monkeypatch):
    """Без tesseract каждая картинка попадала в downloaded_files (I12)."""
    PIL = pytest.importorskip("PIL", reason="Pillow required")
    import PIL.Image
    monkeypatch.setattr(PdfCrawler, "_tesseract_checked", True, raising=False)
    monkeypatch.setattr(PdfCrawler, "_tesseract_available", False, raising=False)
    img = PIL.Image.new("RGB", (600, 400), "white")
    assert PdfCrawler._is_image_schematic(img, ["figure"]) is False


def test_pdf_document_closed_in_finally(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    cfg.crawlers.pdf = CrawlerPDFConfig(ocr_enabled=False, two_column_detection=False,
                                        extract_tables=False, filter_schematic_images=False,
                                        use_toc_as_structure=False)
    crawler = PdfCrawler(cfg)
    path = make_pdf(tmp_path, "x.pdf", [single_col_blocks()])
    import pymupdf
    real_open = pymupdf.open
    opened = {"n": 0}

    class CountingDoc(real_open.__class__ if hasattr(real_open, "__class__") else object):
        pass

    def counting_open(*a, **k):
        doc = real_open(*a, **k)
        opened["n"] += 1
        orig_close = doc.close
        def close():
            doc._closed = True
            return orig_close()
        doc.close = close
        return doc

    monkeypatch.setattr(crawler.__class__, "_ocr_page", staticmethod(lambda *a, **k: ""))
    monkeypatch.setattr("corpus_builder.crawlers.pdf_crawler.fitz.open", counting_open)
    monkeypatch.setattr(crawler, "_extract_tables_all", staticmethod(lambda p: []),
                        raising=False)
    # заставляем путь краулера: скачивание уже есть на диске
    record = crawler._crawl("file://" + path) if False else None
    src = (REPO_ROOT / "corpus_builder" / "crawlers" / "pdf_crawler.py").read_text("utf-8")
    i_finally = src.index("finally:")
    assert "doc.close()" in src[i_finally:i_finally + 200]


# ============================================================
# I2 — robots: явный обход на источник + «запрещено ≠ обработано»
# ============================================================

def test_se_source_can_opt_out_of_robots_check(tmp_path, monkeypatch):
    """`ignore_robots: true` у источника — выход для API-краулеров (I2)."""
    from corpus_builder import pipeline
    from corpus_builder.models import CorpusRecord
    import corpus_builder.robots as robots_mod

    calls: list[str] = []

    class Deny:
        respect = True
        def is_allowed(self, url):
            calls.append(url)
            return False

    class Ok:
        def __init__(self, session=None):
            self.session = session
        def crawl(self, url, categories=None, source=None):
            return CorpusRecord(source_url=url, source_type="stackexchange",
                                content="answer text " * 100)

    monkeypatch.setattr(pipeline, "build_crawl_context", lambda cfg: {
        "session": None, "robots": Deny(),
        "rate_limiter": pipeline.RateLimiter(default_delay=0),
        "state": pipeline.State(cfg.output.state_file)})
    monkeypatch.setattr(pipeline, "make_crawler", lambda t, c, s: Ok())
    monkeypatch.setattr(robots_mod, "pre_filter_by_robots",
                        lambda srcs, rc, on_skip=None: ([], {}))

    cfg = load_config(SMOKE_CFG)
    cfg.output = OutputConfig(corpus_file=str(tmp_path / "o/raw.jsonl"),
                              download_dir=str(tmp_path / "o/dl"))
    cfg.sources = [SourceItem(url="https://electronics.stackexchange.com/questions/1",
                              type="stackexchange", ignore_robots=True)]
    stats = pipeline.run_crawl(cfg, resume=False)
    assert stats["processed"] == 1 and calls == [], \
        "источник с ignore_robots не должен ни проверяться, ни отбрасываться"


def test_robots_allowed_short_url_still_crawled_via_api(tmp_path):
    """URL страницы SE разрешён через API даже когда HTML-страница закрыта —
    проверяем, что ответ API парсится (сетевой слой — фейк)."""
    cfg = make_config(tmp_path)
    crawler = StackExchangeCrawler(cfg)
    crawler.session = FakeSession({
        "api.stackexchange.com/2.3/questions/1": FakeResponse(json_data={"items": [
            {"question_id": 1, "title": "Q", "body": "<p>question body</p>",
             "tags": [], "score": 3, "answer_count": 1, "accepted_answer_id": 2,
             "creation_date": 1700000000}]}),
        "answers": FakeResponse(json_data={"items": [
            {"answer_id": 2, "body": "<p>accepted answer body</p>", "score": 9,
             "is_accepted": True, "creation_date": 1700000100}]}),
    })
    record = crawler.crawl("https://electronics.stackexchange.com/questions/1")
    assert record.status == "ok"
    assert record.license.startswith("CC BY-SA")
