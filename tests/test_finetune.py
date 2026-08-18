"""Тесты на fine-tuning модули."""
import json
import pytest
from pathlib import Path


# === FormatConverter ===

def test_format_jsonl(tmp_path):
    from corpus_builder.postproc.format_converter import FormatConverter
    infile = tmp_path / "pairs.jsonl"
    infile.write_text(json.dumps({"prompt": "Q", "completion": "A", "task_type": "test"}) + "\n")
    outfile = tmp_path / "out.jsonl"
    stats = FormatConverter.convert(infile, outfile, "jsonl")
    assert stats["count"] == 1
    data = json.loads(outfile.read_text())
    assert data["prompt"] == "Q"

def test_format_chatml(tmp_path):
    from corpus_builder.postproc.format_converter import FormatConverter
    infile = tmp_path / "pairs.jsonl"
    infile.write_text(json.dumps({"prompt": "Hello", "completion": "Hi"}) + "\n")
    outfile = tmp_path / "chatml.jsonl"
    FormatConverter.convert(infile, outfile, "chatml")
    data = json.loads(outfile.read_text())
    assert "<|im_start|>" in data["text"]

def test_format_alpaca(tmp_path):
    from corpus_builder.postproc.format_converter import FormatConverter
    infile = tmp_path / "pairs.jsonl"
    infile.write_text(json.dumps({"prompt": "Do X\nInput: Y", "completion": "Result"}) + "\n")
    outfile = tmp_path / "alpaca.jsonl"
    FormatConverter.convert(infile, outfile, "alpaca")
    data = json.loads(outfile.read_text())
    assert "instruction" in data
    assert "output" in data

def test_format_sharegpt(tmp_path):
    from corpus_builder.postproc.format_converter import FormatConverter
    infile = tmp_path / "pairs.jsonl"
    infile.write_text(json.dumps({"prompt": "Q", "completion": "A"}) + "\n")
    outfile = tmp_path / "sg.jsonl"
    FormatConverter.convert(infile, outfile, "sharegpt")
    data = json.loads(outfile.read_text())
    assert "conversations" in data
    assert data["conversations"][0]["from"] == "human"

def test_format_unknown():
    from corpus_builder.postproc.format_converter import FormatConverter
    with pytest.raises(ValueError):
        FormatConverter.convert("x", "y", "unknown_format")


# === QualityFilter ===

def test_quality_pass():
    from corpus_builder.postproc.quality_finetune import passes_finetune_quality
    ok, reason = passes_finetune_quality({"prompt": "What is electronics and circuits?", "completion": "Electronics is the study of circuits."})
    assert ok is True

def test_quality_short_prompt():
    from corpus_builder.postproc.quality_finetune import passes_finetune_quality
    ok, reason = passes_finetune_quality({"prompt": "Hi", "completion": "Long answer"})
    assert ok is False
    assert "short" in reason

def test_quality_duplicate():
    from corpus_builder.postproc.quality_finetune import passes_finetune_quality
    ok, reason = passes_finetune_quality({"prompt": "Same text here for testing", "completion": "Same text here for testing"})
    assert ok is False
    assert "equals" in reason


# === DatasetBalancer ===

def test_balance_max():
    from corpus_builder.postproc.dataset_balancer import balance_by_type
    pairs = [{"task_type": "qa"}] * 100 + [{"task_type": "code"}] * 50
    result = balance_by_type(pairs, max_per_type=30)
    qa_count = sum(1 for p in result if p["task_type"] == "qa")
    assert qa_count == 30

def test_balance_stats():
    from corpus_builder.postproc.dataset_balancer import get_balance_stats
    pairs = [{"task_type": "qa"}, {"task_type": "qa"}, {"task_type": "code"}]
    stats = get_balance_stats(pairs)
    assert stats["total"] == 3
    assert stats["by_type"]["qa"] == 2
    assert stats["num_types"] == 2


# === PIIFilter ===

def test_pii_email():
    from corpus_builder.postproc.pii_filter import remove_pii
    assert "[REDACTED]" in remove_pii("Contact: user@example.com")

def test_pii_phone():
    from corpus_builder.postproc.pii_filter import remove_pii
    assert "[REDACTED]" in remove_pii("Call +1-555-123-4567")

def test_pii_clean_pair():
    from corpus_builder.postproc.pii_filter import clean_pair
    pair = {"prompt": "Email: test@test.com", "completion": "OK"}
    result = clean_pair(pair)
    assert "[REDACTED]" in result["prompt"]
    assert result["completion"] == "OK"


# === Chunker ===

def test_chunk_short():
    from corpus_builder.postproc.chunker import chunk_text
    result = chunk_text("Short text.", max_chars=100)
    assert len(result) == 1

def test_chunk_long():
    from corpus_builder.postproc.chunker import chunk_text
    text = "Sentence. " * 1000
    result = chunk_text(text, max_chars=200)
    assert len(result) > 1


# === Models ===

def test_finetune_config_defaults():
    from corpus_builder.models import FineTuneConfig
    cfg = FineTuneConfig()
    assert cfg.max_per_type == 1000
    assert cfg.balance_classes is True
    assert "chatml" in cfg.formats


# === InstructionGenerator ===

def test_instruction_generator_article_summary(tmp_path):
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    corpus = tmp_path / "corpus.jsonl"
    long_text = "This is the first paragraph about electronics. " * 100 + "\n\nSecond paragraph. " * 50
    corpus.write_text(json.dumps({"content": long_text, "source_url": "https://example.com"}) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_article_summary(corpus, 10)
    assert len(pairs) > 0
    assert pairs[0]["task_type"] == "article_summary"
