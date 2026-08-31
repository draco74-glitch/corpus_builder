"""Регрессии: качество данных, экспорт, локализация, «мелочи» из ревью.

C1 (причины отбраковки), I12 (PII), I13/I20 (prompt_variations), I14 (RU-маркеры),
I15 (экспорт = то, что записал crawler), I16 (parquet nulls), I17 (мёртвый код),
I18 (приватные поля State), I19 (i18n-дубли), Minor (F821/utcnow/B033).
"""
from __future__ import annotations

import ast
import json
from datetime import timezone
from pathlib import Path

import pytest

from corpus_builder.models import CorpusRecord
from corpus_builder.postproc import prompt_variations as pv
from corpus_builder.postproc.dataset_balancer import balance_by_type, get_balance_stats
from corpus_builder.postproc.export import export_huggingface, export_parquet
from corpus_builder.postproc.extract_pairs import extract_qa_pairs, run_extract_pairs
from corpus_builder.postproc.instruction_generator import InstructionGenerator
from corpus_builder.postproc.pii_filter import detect_pii, remove_pii

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "corpus_builder"


# ============================================================
# C1 — честные причины отбраковки
# ============================================================

def test_export_hf_keeps_record_fields(tmp_path):
    rows = [
        {"source_url": "http://a/1", "source_type": "github_repo",
         "content": "body", "language": "en", "license": "MIT",
         "content_sha1": "abc", "quality_score": 0.5,
         "downloaded_files": [{"type": "kicad", "local_path": "/x/kicad_lib/1_footer.kicad_sch",
                               "sha1": "s1", "size_bytes": 100}],
         "metadata": {"repo_name": "r", "project_tree": ["README.md"]},
         "categories": ["c"], "date_accessed": "2026-01-01T00:00:00+00:00",
         "is_duplicate": False},
    ]
    src = tmp_path / "corpus_final.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = export_huggingface(src, tmp_path / "hf")

    exported = [json.loads(l) for l in (tmp_path / "hf" / "data.jsonl").read_text().splitlines()]
    assert exported[0]["metadata"] == rows[0]["metadata"], \
        "экспорт не должен терять то, что записал краулер (I15)"
    assert exported[0]["downloaded_files"] == rows[0]["downloaded_files"]
    assert "loading_script.py" not in str(out)


def test_export_parquet_keeps_metadata_and_nulls(tmp_path):
    pytest.importorskip("pyarrow")
    _pa = pytest.importorskip("pyarrow")
    rows = [
        {"source_url": "http://a/1", "source_type": "html", "content": "x",
         "language": None, "content_sha1": None, "quality_score": None,
         "license": None, "categories": [], "date_accessed": "d",
         "is_duplicate": False, "downloaded_files": [],
         "metadata": {"title": "T"}},
    ]
    src = tmp_path / "c.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    export_parquet(src, tmp_path / "c.parquet")

    import pyarrow.parquet as pq
    table = pq.read_table(tmp_path / "c.parquet").to_pylist()
    assert table[0]["language"] is None, "unknown обязан быть NULL, а не ''"
    assert table[0]["quality_score"] is None
    assert json.loads(table[0]["metadata"]) == {"title": "T"}, \
        "KiCad-пути и accepted_answer_id нужны потребителю (I15)"


def test_export_dataset_infos_describes_actual_file(tmp_path):
    rows = [{"source_url": f"http://a/{i}", "source_type": "wiki", "content": "hello",
             "language": "en", "quality_score": 0.4, "categories": [], "is_duplicate": False,
             "downloaded_files": [], "metadata": {}, "date_accessed": "x",
             "content_sha1": "s", "license": "CC BY-SA 4.0"} for i in range(3)]
    src = tmp_path / "c.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    export_huggingface(src, tmp_path / "hf")
    infos = json.loads((tmp_path / "hf" / "dataset_infos.json").read_text(encoding="utf-8"))
    feat = infos["corpus_builder"]["features"]
    # declared features обязаны быть подмножеством реальных колонок и описывать
    # служебные поля, которые раньше «терялись» описания
    real_columns = set(rows[0])
    assert set(feat) <= real_columns, f"описание обещает лишнее: {set(feat) - real_columns}"
    assert {"downloaded_files", "metadata", "categories"} <= set(feat)
    readme = (tmp_path / "hf" / "README.md").read_text(encoding="utf-8")
    assert "loading_script" not in readme


# ============================================================
# I12 — PII: контекст вместо резки технических чисел
# ============================================================

@pytest.mark.parametrize("text,expected", [
    ("Set the controller IP address to 192.168.1.1 before use.", "[REDACTED]"),
    ("The firmware bundle 1.22.331.4 shipped in 2019.", None),
    ("Pull-ups were 4.7.10.2 kOhm on this board.", None),
])
def test_pii_ip_only_with_context(text, expected):
    out = remove_pii(text)
    assert ("[REDACTED]" in out) is (expected is not None), out


@pytest.mark.parametrize("text", [
    "Order part 123-45-6789 from the catalogue.",
    "The delay is about 500 123 4567 nanoseconds.",
])
def test_pii_does_not_eat_technical_numbers(text):
    assert remove_pii(text) == text


@pytest.mark.parametrize("text", [
    "Телефон: +7 (495) 123-45-67.",
    "Tel: 1-555-123-4567, contact support.",
])
def test_pii_still_removes_real_phones(text):
    assert "[REDACTED]" in remove_pii(text)


def test_pii_obfuscated_email_is_actually_removed():
    """Раньше заменялись только разделители → адрес оставался читаемым."""
    text = "Contact john.doe [at] example [dot] com for details."
    out = remove_pii(text)
    assert "john.doe" not in out
    assert "example" not in out or ".com" not in out
    assert "[REDACTED]" in out


def test_pii_aggressive_mode_opt_in():
    text = "The delay is about 500 123 4567 nanoseconds."
    assert remove_pii(text) == text
    assert "[REDACTED]" in remove_pii(text, aggressive=True)


def test_detect_pii_matches_remove_pii():
    assert "phone" in detect_pii("Телефон: +7 495 123-45-67") or \
           "phone" in detect_pii("Call +1-555-123-4567")
    assert detect_pii("Order 123-45-6789 please") == {}


# ============================================================
# I13/I20 — prompt_variations: никаких сырых {placeholder}
# ============================================================

def test_prompt_fallback_never_leaks_braces(monkeypatch):
    monkeypatch.setitem(pv.PROMPT_VARIATIONS, "concept_explanation",
                        ["Explain {titel} please", "Define: {heading}"])
    out = pv.get_prompt("concept_explanation", heading="Op-amp")
    assert out == "Define: Op-amp"


def test_prompt_all_templates_broken_gives_clean_text(monkeypatch):
    monkeypatch.setitem(pv.PROMPT_VARIATIONS, "concept_explanation",
                        ["only {nope} template"])
    out = pv.get_prompt("concept_explanation", heading="Op-amp")
    assert "{" not in out and "}" not in out
    assert "Op-amp" in out


def test_custom_prompts_file_does_not_drop_defaults(monkeypatch, tmp_path):
    """merge_custom_prompts не должен затираать встроенные варианты."""
    monkeypatch.chdir(tmp_path)
    Path(tmp_path / "prompts.yaml").write_text(
        "article_summary:\n  - 'Кастомный: {content}'\n", encoding="utf-8")
    defaults_before = pv.get_variation_count("concept_explanation")
    assert pv.merge_custom_prompts("prompts.yaml") is True
    # кастомные варианты ДОБАВЛЯЮТСЯ к встроенным (не затирают их)
    assert pv.get_variation_count("article_summary") == 8
    assert pv.get_variation_count("concept_explanation") == defaults_before
    assert any("Кастомный" in v for v in pv.list_variations("article_summary"))


# ============================================================
# I14 — маркеры вопросов/ответов: RU и EN одинаково
# ============================================================

EN_THREAD = ("# Debouncing an encoder\n\n## Question\n\n"
             "I get five counts per click from a mechanical switch, how do I debounce "
             "it in hardware? Any RC values that work well?\n\n"
             "## Answer (score=12) [ACCEPTED]\n\nUse an RC filter: 100 nF and 10k in "
             "parallel with the switch, then a Schmitt trigger cleans the edge.\n\n"
             "## Answer (score=1)\n\nSoftware sampling at 5 ms works too.\n")

RU_THREAD = ("# Борьба с дребезгом\n\n## Вопрос\n\n"
             "Механическая кнопка даёт пять срабатываний на нажатие, как подавить "
             "дребезг на железе и какие номиналы подобрать?\n\n"
             "## Ответ (score=12) [ПРИНЯТ]\n\nRC-цепочка 100 нФ и 10 кОм плюс триггер "
             "Шмитта, дальше микроконтроллер видит один фронт.\n\n"
             "## Ответ (score=2)\n\nМожно опрашивать таймером раз в 5 мс.\n")


@pytest.mark.parametrize("content,meta,expect_prefix", [
    (EN_THREAD, {"title": "Debouncing an encoder", "tags": ["encoder"],
     "accepted_answer_id": 11,
     "answers": [{"answer_id": 11, "score": 12, "is_accepted": True},
                 {"answer_id": 12, "score": 1, "is_accepted": False}]}, "Question:"),
    (RU_THREAD, {"title": "Борьба с дребезгом", "tags": ["кнопка"],
     "accepted_answer_id": 21,
     "answers": [{"answer_id": 21, "score": 12, "is_accepted": True},
                 {"answer_id": 22, "score": 2, "is_accepted": False}]}, "Вопрос:"),
])
def test_se_pairs_for_both_languages(content, meta, expect_prefix):
    record = {"source_url": "http://se/1", "source_type": "stackexchange",
              "content": content, "status": "ok", "metadata": meta}
    pairs = extract_qa_pairs(record)
    assert len(pairs) == 1, "пары обязаны извлекаться для обоих языков"
    assert pairs[0]["prompt"].startswith(expect_prefix)
    assert "RC" in pairs[0]["completion"], "нужен принятый ответ, а не первый попавшийся"


def test_pipeline_and_ft_share_task_type_vocabulary(tmp_path):
    """Один и тот же корпус двумя путями даёт одинаковые имена типов (I14)."""
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps({
        "source_url": "http://se/1", "source_type": "stackexchange", "content": EN_THREAD,
        "status": "ok", "metadata": {"title": "Debouncing", "answers": [
            {"answer_id": 11, "score": 12, "is_accepted": True}]}},
        ensure_ascii=False) + "\n" + json.dumps({
        "source_url": "http://a", "source_type": "html",
        "content": "# T\n\n" + "\n\n".join(
            f"Paragraph {i} explains a design decision in detail. Extra sentence "
            f"number {i} keeps the paragraph long enough to be summarised properly."
            for i in range(20)),
        "status": "ok"}), encoding="utf-8")

    pipeline_out = tmp_path / "p.jsonl"
    run_extract_pairs(corpus, pipeline_out)
    legacy_types = {json.loads(l)["task_type"] for l in pipeline_out.read_text().splitlines()}

    ft_types = {p["task_type"] for p in InstructionGenerator().generate_from_corpus(
        corpus, max_per_type=20)}

    known = {"qa_stackexchange", "article_summary"}
    assert legacy_types & known, legacy_types
    assert ft_types & known, ft_types
    # ни один из путей не порождает «своих» синонимов для одних и тех же типов
    assert not {t for t in legacy_types | ft_types
                if t.endswith(("_to_summary", "_to_article", "_to_explanation",
                              "_to_specs", "_to_structure"))}


def test_prompts_come_from_variations_not_hardcoded_language(tmp_path):
    """Шаблоны пар берутся из prompt_variations (настраиваемо, I14)."""
    rows = [{"source_url": "http://a", "source_type": "html", "status": "ok",
             "content": "# T\n\n" + "\n\n".join(
                 f"Paragraph {i} about design. Second sentence number {i} is long "
                 f"enough to satisfy the summariser heuristics here." for i in range(20))}]
    src = tmp_path / "c.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = tmp_path / "pairs.jsonl"
    run_extract_pairs(src, out)
    prompts = [json.loads(l)["prompt"] for l in out.read_text().splitlines()]
    assert prompts
    registered = set().union(*(set(pv.list_variations(t)) for t in
                               ("article_summary", "article_expansion")))
    assert any(any(t in p for t in ("Summar", "summar", "TL;DR", "Expand", "Write a detailed"))
               for p in prompts), prompts


def test_run_extract_pairs_reports_ru_and_en(tmp_path):
    rows = [
        {"source_url": "http://se/en", "source_type": "stackexchange",
         "content": EN_THREAD, "status": "ok",
         "metadata": {"title": "Debouncing", "answers": [
             {"answer_id": 11, "score": 12, "is_accepted": True}]}},
        {"source_url": "http://se/ru", "source_type": "stackexchange",
         "content": RU_THREAD, "status": "ok",
         "metadata": {"title": "Дребезг", "answers": [
             {"answer_id": 21, "score": 12, "is_accepted": True}]}},
    ]
    src = tmp_path / "in.jsonl"
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), "utf-8")
    stats = run_extract_pairs(src, tmp_path / "out.jsonl")
    assert stats["by_type"].get("qa_stackexchange") == 2


def test_ft_generator_handles_english_threads(tmp_path):
    src = tmp_path / "corpus.jsonl"
    src.write_text(json.dumps({
        "source_url": "http://se/en", "source_type": "stackexchange",
        "content": EN_THREAD, "status": "ok",
        "metadata": {"title": "Debouncing", "answers": [
            {"answer_id": 11, "score": 12, "is_accepted": True},
            {"answer_id": 12, "score": 1, "is_accepted": False}]}},
        ensure_ascii=False) + "\n", encoding="utf-8")
    gen = InstructionGenerator()
    pairs = gen.generate_from_corpus(src, max_per_type=10)
    types = {p["task_type"] for p in pairs}
    assert "qa_stackexchange" in types
    assert "multi_turn_dialogue" in types, "многоходовые диалоги строились только из RU"
    assert not any(p["prompt"].startswith("## ") for p in pairs)


# ============================================================
# I15/I16balancer + инструкции
# ============================================================

def test_balance_by_type_reports_dropped():
    pairs = [{"prompt": f"q{i}", "completion": f"a{i}",
              "task_type": "big" if i < 10 else "small"} for i in range(12)]
    out = balance_by_type(pairs, max_per_type=2)
    stats = get_balance_stats(out, original=pairs)
    assert stats["by_type"]["big"] == 2
    assert stats["dropped_by_type"]["big"] == 8, "потеря обязана быть видна"


def test_article_summary_uses_first_sentence_of_each_paragraph(tmp_path):
    article = "\n\n".join(
        f"Paragraph {i} starts with a sentence about amplifier design. "
        f"The second sentence of paragraph {i} adds detail that must not leak."
        for i in range(8))
    src = tmp_path / "c.jsonl"
    src.write_text(json.dumps({"source_url": "http://a", "source_type": "html",
                              "content": f"# Title\n\n{article}", "status": "ok"}))
    pairs = InstructionGenerator().generate_from_corpus(
        src, max_per_type=5, task_types=["article_summary"])
    sums = [p for p in pairs if p["task_type"] == "article_summary"]
    assert sums and len(sums[0]["completion"]) > 10


# ============================================================
# I17/I18/I19 — мёртвый код, приватные поля, дубли словарей
# ============================================================

def _prod_references(symbol: str) -> int:
    count = 0
    for path in SRC.rglob("*.py"):
        if path.name in {"__init__.py", "writer.py"} and symbol in {"CorpusWriter", "GzipCorpusWriter"}:
            continue
        text = path.read_text(encoding="utf-8")
        if path.name == "writer.py" and symbol in {"CorpusWriter", "GzipCorpusWriter"}:
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Name) and node.id == symbol or isinstance(node, ast.Attribute) and node.attr == symbol:
                count += 1
    return count


@pytest.mark.parametrize("module", ["http_cache", "proxy_rotator", "parallel_postproc",
                                     "incremental_dedup", "mmap_reader"])
def test_previously_dead_modules_are_now_reachable(module):
    """Раньше эти модули были «реализованы, но не вызывались» (I3/I17)."""
    files = [p for p in SRC.rglob("*.py") if p.name != f"{module}.py"]
    users = [p.name for p in files
             if f"from .{module} import" in p.read_text(encoding="utf-8")
             or f"from ..{module} import" in p.read_text(encoding="utf-8")
             or f"from corpus_builder.{module} import" in p.read_text("utf-8")]
    assert users, f"{module} по-прежнему не имеет потребителей"


@pytest.mark.parametrize("symbol", ["pre_filter_by_robots", "run_dedup_adaptive",
                                     "make_cached_session"])
def test_key_functions_have_prod_callers(symbol):
    assert _prod_references(symbol) >= 1, f"{symbol} нигде не вызывается"


def test_pipeline_does_not_touch_state_privates():
    src = (SRC / "pipeline.py").read_text(encoding="utf-8")
    assert "state._done" not in src and "state._errors" not in src, \
        "используйте публичный API State (I18)"
    assert "state.reset()" in src and "state.clear_errors()" in src


def test_make_session_exists_once():
    """Дубль make_session из http.py удалён (I17)."""
    http_src = (SRC / "http.py").read_text(encoding="utf-8")
    assert "def make_session" not in http_src
    import corpus_builder.robots as robots
    assert hasattr(robots, "make_session")


def test_i18n_dicts_have_no_duplicate_keys():
    """18 дублей ключей в словарях перевода — мёртвые записи (I19)."""
    tree = ast.parse((SRC / "gui_improvements.py").read_text(encoding="utf-8"))
    dupes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            if len(keys) > 20:                    # только большие словари переводов
                dupes += [k for k in set(keys) if keys.count(k) > 1]
    assert not dupes, f"дублирующиеся ключи перевода: {sorted(set(dupes))[:5]}"


def test_no_undefined_names_in_package():
    """F821: `Callable`/`_random`/`log` использовались без импорта (M1, C5)."""
    import shutil
    import subprocess
    import sys
    if not shutil.which("ruff"):
        pytest.skip("ruff is not installed")
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "F821", "--output-format",
         "concise", str(SRC)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_spam_patterns_have_no_duplicate_stopword():
    src = (SRC / "postproc" / "instruction_generator.py").read_text(encoding="utf-8")
    assert src.count('"into"') == 1, "дубликат в stop_words (B033)"


def test_dates_are_timezone_aware():
    rec = CorpusRecord(source_url="http://a", source_type="html", content="x")
    assert "+00:00" in rec.date_accessed, "naive utcnow() ломает статистику по дням"
    from datetime import datetime
    assert datetime.fromisoformat(rec.date_accessed).tzinfo is not None
    assert datetime.fromisoformat(rec.date_accessed).tzinfo == timezone.utc


def test_i18n_every_used_key_is_translated():
    """Ни одного tr("…") без перевода — иначе в GUI всплывает английский/ключ.

    Раньше проверка была только реактивной: лог «Нет перевода для ключа» при
    запуске. Такой тест ловит недостающий ключ до коммита.
    """
    import ast
    import collections
    from pathlib import Path

    from corpus_builder.gui_improvements import TRANSLATIONS

    used: dict[str, set[str]] = collections.defaultdict(set)
    for f in sorted(Path("corpus_builder").rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                used[node.args[0].value].add(f.name)

    assert len(used) > 100, f"слишком мало ключей — вероятно, сломался сбор: {len(used)}"
    for lang in ("ru", "en"):
        table = TRANSLATIONS[lang]
        missing = {k: sorted(v) for k in used if k not in table for v in [used[k]]}
        assert not missing, f"нет перевода ({lang}): {missing}"
