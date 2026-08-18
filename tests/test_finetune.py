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
    # Need proper paragraphs with sentences ending in periods.
    # Use realistic multi-paragraph article so chunker doesn\'t collapse it.
    paragraphs = []
    for i in range(20):
        paragraphs.append(
            f"This is the first sentence of paragraph {i} about electronics. "
            f"This is the second sentence of paragraph {i} about circuits. "
            f"This is the third sentence of paragraph {i} about PCB design."
        )
    long_text = "\n\n".join(paragraphs)  # 20 paragraphs, ~3000 chars total
    corpus.write_text(json.dumps({"content": long_text, "source_url": "https://example.com"}) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_article_summary(corpus, 10)
    assert len(pairs) > 0
    assert pairs[0]["task_type"] == "article_summary"
    # Summary should contain first sentences, not the whole first paragraph
    assert "first sentence" in pairs[0]["completion"].lower()


def test_article_summary_chunks_long_article(tmp_path):
    """Bug 11: Long articles should be chunked, generating multiple summaries."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    corpus = tmp_path / "corpus.jsonl"
    # Build a 15000-char article with distinct content in each 4000-char segment
    paragraphs = []
    for i in range(50):
        paragraphs.append(
            f"Paragraph {i} discusses electronics topic number {i}. "
            f"This is the second sentence about circuits in paragraph {i}. "
            f"This is the third sentence about PCB design in paragraph {i}."
        )
    long_text = "\n\n".join(paragraphs)  # ~12000 chars
    assert len(long_text) > 8000  # ensure it will be chunked
    corpus.write_text(json.dumps({"content": long_text, "source_url": "https://example.com"}) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_article_summary(corpus, 100)
    # Should produce multiple pairs (one per chunk)
    assert len(pairs) >= 2, f"Expected >=2 pairs from chunked article, got {len(pairs)}"
    # Each pair should have a different prompt (different chunk content)
    prompts = [p["prompt"] for p in pairs]
    assert len(set(prompts)) == len(prompts), "All chunk prompts should be unique"



def test_translation_disabled(tmp_path):
    """Bug 1: Translation should return empty (no parallel corpora)."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps({
        "content": "Some text in Russian about electronics.",
        "language": "ru",
        "source_url": "https://example.com"
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_translation(corpus, 10)
    assert len(pairs) == 0  # Should be empty


def test_qa_pairs_english_markers(tmp_path):
    """Bug 6: Q&A should work with English markers too."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    corpus = tmp_path / "corpus.jsonl"
    content = (
        "# Test Question\n\n"
        "## Question\n\nWhat is an operational amplifier and how does it work?\n\n"
        "## Answer\n\n[ACCEPTED] An op-amp is a DC-coupled high-gain amplifier.\n\n"
    )
    corpus.write_text(json.dumps({
        "content": content,
        "source_type": "stackexchange",
        "source_url": "https://electronics.stackexchange.com/q/123",
        "metadata": {"title": "What is an op-amp?"}
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_qa_pairs(corpus, 10)
    assert len(pairs) > 0
    assert "op-amp" in pairs[0]["completion"].lower() or "amplifier" in pairs[0]["completion"].lower()


def test_code_explanation_finds_context(tmp_path):
    """Bug 4: Code explanation should find explanatory sentences."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    corpus = tmp_path / "corpus.jsonl"
    content = (
        "This function calculates the voltage drop across a resistor. "
        "The following code shows the implementation of Ohm's law.\n\n"
        "```python\ndef calc_voltage(current, resistance):\n    \"\"\"Calculate voltage using Ohm's law.\"\"\"\n    return current * resistance\n```\n\n"
        "This code is part of the power module and handles voltage calculations."
    )
    corpus.write_text(json.dumps({"content": content, "source_url": "https://example.com"}) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_code_explanation(corpus, 10)
    assert len(pairs) > 0
    # Completion should contain explanatory text, not just "This code is part of:"
    assert "voltage" in pairs[0]["completion"].lower() or "power" in pairs[0]["completion"].lower()



# ============================================================
# Bug 8: dedup_pairs
# ============================================================

def test_dedup_pairs_exact_duplicates():
    """Exact duplicates (same prompt + same completion) should be removed."""
    from corpus_builder.postproc.quality_finetune import dedup_pairs
    pairs = [
        {"prompt": "What is electronics?", "completion": "Study of circuits.", "task_type": "qa"},
        {"prompt": "What is electronics?", "completion": "Study of circuits.", "task_type": "qa"},
        {"prompt": "Different question here.", "completion": "Different answer here.", "task_type": "qa"},
    ]
    result, stats = dedup_pairs(pairs)
    assert len(result) == 2
    assert stats["removed"] == 1
    assert stats["duplicates"] == 1


def test_dedup_pairs_prompt_mode():
    """In 'prompt' mode, same prompt with different completion is a dup."""
    from corpus_builder.postproc.quality_finetune import dedup_pairs
    pairs = [
        {"prompt": "Same prompt here for testing.", "completion": "Answer A", "task_type": "qa"},
        {"prompt": "Same prompt here for testing.", "completion": "Answer B", "task_type": "qa"},
    ]
    result, _ = dedup_pairs(pairs, mode="prompt")
    assert len(result) == 1


def test_dedup_pairs_normalized_mode():
    """Normalized mode collapses whitespace + case differences."""
    from corpus_builder.postproc.quality_finetune import dedup_pairs
    pairs = [
        {"prompt": "What   is electronics?", "completion": "A", "task_type": "qa"},
        {"prompt": "what is electronics?", "completion": "B", "task_type": "qa"},
    ]
    result, _ = dedup_pairs(pairs, mode="prompt_normalized")
    assert len(result) == 1


def test_dedup_pairs_empty():
    from corpus_builder.postproc.quality_finetune import dedup_pairs
    result, stats = dedup_pairs([])
    assert len(result) == 0
    assert stats["input"] == 0


def test_dedup_pairs_keep_last():
    """keep='last' should preserve the last occurrence."""
    from corpus_builder.postproc.quality_finetune import dedup_pairs
    pairs = [
        {"prompt": "Q", "completion": "First answer here.", "task_type": "qa"},
        {"prompt": "Q", "completion": "Second answer here.", "task_type": "qa"},
    ]
    result, _ = dedup_pairs(pairs, mode="prompt", keep="last")
    assert len(result) == 1
    assert result[0]["completion"] == "Second answer here."


# ============================================================
# Bug 9: token_utils
# ============================================================

def test_token_count_basic():
    from corpus_builder.postproc.token_utils import count_tokens
    n = count_tokens("Hello world")
    assert n > 0
    assert isinstance(n, int)


def test_token_count_empty():
    from corpus_builder.postproc.token_utils import count_tokens
    assert count_tokens("") == 0
    assert count_tokens(None) == 0


def test_token_estimate_heuristic():
    """Heuristic estimate should be in a reasonable range."""
    from corpus_builder.postproc.token_utils import estimate_tokens
    text = "Hello world this is a test of the token estimator."
    n = estimate_tokens(text, lang="en")
    # ~52 chars / 4 chars-per-token ≈ 13 tokens
    assert 5 <= n <= 25


def test_token_count_russian_vs_english():
    """Russian text should produce more tokens per char than English."""
    from corpus_builder.postproc.token_utils import count_tokens
    # 100 chars each
    en_text = "Electronics is the study of circuits and current flow. " * 2  # ~100 chars
    ru_text = "Электроника это наука о цепях и протекании тока в схемах. " * 2  # ~100 chars
    en_tokens = count_tokens(en_text)
    ru_tokens = count_tokens(ru_text)
    # Russian should have MORE tokens for the same char count
    assert ru_tokens > en_tokens, (
        f"Russian ({ru_tokens} tokens) should > English ({en_tokens} tokens) "
        f"for similar char counts"
    )


def test_token_pair_count():
    from corpus_builder.postproc.token_utils import count_pair_tokens
    pair = {"prompt": "What is electronics?", "completion": "It is the study of circuits."}
    result = count_pair_tokens(pair)
    assert "prompt_tokens" in result
    assert "completion_tokens" in result
    assert "total_tokens" in result
    assert result["total_tokens"] == result["prompt_tokens"] + result["completion_tokens"]


def test_token_passes_limits():
    from corpus_builder.postproc.token_utils import passes_token_limits
    ok, reason = passes_token_limits(
        {"prompt": "What is electronics and how does it work in circuits?",
         "completion": "Electronics is the study of electronic circuits and current flow."},
        min_prompt_tokens=3, max_prompt_tokens=100,
        min_completion_tokens=3, max_completion_tokens=100,
    )
    assert ok is True


def test_token_passes_limits_too_many():
    from corpus_builder.postproc.token_utils import passes_token_limits
    ok, reason = passes_token_limits(
        {"prompt": "x" * 10000, "completion": "answer"},
        max_prompt_tokens=100,
    )
    assert ok is False
    assert "too_many" in reason


# ============================================================
# Bug 10: Enhanced PII filter
# ============================================================

def test_pii_obfuscated_email_brackets():
    """user [at] example [dot] com should be redacted."""
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "Contact: user [at] example [dot] com for details"
    result = remove_pii(text)
    assert "user [at] example [dot] com" not in result
    assert "[REDACTED]" in result


def test_pii_obfuscated_email_parens():
    """user(at)example(dot)com should be redacted."""
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "Email: user(at)example(dot)com"
    result = remove_pii(text)
    assert "user(at)example(dot)com" not in result


def test_pii_obfuscated_email_plain():
    """user at example dot com (no brackets) should be redacted."""
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "Reach me at user at example dot com"
    result = remove_pii(text)
    # Should not contain the original obfuscated form
    assert "user at example dot com" not in result


def test_pii_api_key_assignment():
    """api_key="sk-..." should have value redacted, key name kept."""
    from corpus_builder.postproc.pii_filter import remove_pii
    text = 'api_key="sk-abc123def456ghi789jkl012mno345pqr678"'
    result = remove_pii(text)
    assert "sk-abc123def456ghi789jkl012mno345pqr678" not in result
    assert "api_key" in result  # key name preserved
    assert "[REDACTED]" in result


def test_pii_token_assignment():
    """token=ghp_... should have value redacted."""
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "token=ghp_abcdefghijklmnopqrstuvwxyz0123456789AB"
    result = remove_pii(text)
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789AB" not in result
    assert "[REDACTED]" in result


def test_pii_bearer_token():
    """Bearer <jwt> should have token redacted, 'Bearer' kept."""
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
    result = remove_pii(text)
    assert "Bearer" in result
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig" not in result


def test_pii_openai_key():
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "key=sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
    result = remove_pii(text)
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789" not in result


def test_pii_github_pat():
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789AB"
    result = remove_pii(text)
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789AB" not in result


def test_pii_jwt():
    from corpus_builder.postproc.pii_filter import remove_pii
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    text = f"jwt={jwt}"
    result = remove_pii(text)
    assert jwt not in result


def test_pii_aws_key():
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
    result = remove_pii(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in result


def test_pii_slack_token():
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "SLACK_TOKEN=xoxb-1234567890-abcdefghij"
    result = remove_pii(text)
    assert "xoxb-1234567890-abcdefghij" not in result


def test_pii_github_url_at_username():
    """github.com/@username should be redacted."""
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "Clone from github.com/@secretuser/repo"
    result = remove_pii(text)
    assert "@secretuser" not in result


def test_pii_git_ssh_url():
    """git@github.com:user/repo should have host part redacted."""
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "git@github.com:secretuser/repo.git"
    result = remove_pii(text)
    assert "[REDACTED]" in result


def test_pii_casual_mention_preserved():
    """Casual @username in prose should NOT be redacted."""
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "See @torvalds comment on the Linux kernel issue."
    result = remove_pii(text)
    assert "@torvalds" in result  # preserved


def test_pii_russian_phone():
    """Russian phone without + (8-495-...) should be redacted."""
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "Phone: 8 (495) 123-45-67"
    result = remove_pii(text)
    assert "8 (495) 123-45-67" not in result


def test_pii_international_phone_no_plus():
    """1-555-123-4567 without + should be redacted."""
    from corpus_builder.postproc.pii_filter import remove_pii
    text = "Call 1-555-123-4567 anytime"
    result = remove_pii(text)
    assert "1-555-123-4567" not in result


def test_pii_detect_returns_types():
    from corpus_builder.postproc.pii_filter import detect_pii
    text = "Email: user@example.com, key=sk-abc123def456ghi789jkl012"
    found = detect_pii(text)
    assert "email" in found
    assert "api_key_or_token" in found or "bare_token" in found


# ============================================================
# Bug 11: Chunker integration
# ============================================================

def test_chunker_preserves_paragraphs():
    """Chunker should preserve paragraph boundaries (\n\n) within chunks
    when multiple paragraphs fit in one chunk.
    """
    from corpus_builder.postproc.chunker import chunk_text
    # 3 short paragraphs that all fit in one chunk of 200 chars
    text = (
        "First paragraph about electronics. Second sentence here.\n\n"
        "Second paragraph about circuits. Another sentence.\n\n"
        "Third paragraph about PCB design. Final sentence."
    )
    chunks = chunk_text(text, max_chars=200)
    # Should fit in a single chunk
    assert len(chunks) == 1
    # The paragraph breaks should be preserved
    assert "\n\n" in chunks[0], f"Paragraph breaks lost: {chunks[0]!r}"
    # Should have 3 distinct paragraphs when split
    paras = [p for p in chunks[0].split("\n\n") if p.strip()]
    assert len(paras) == 3


def test_chunker_single_paragraph_long():
    """Chunker should split a single long paragraph by sentences."""
    from corpus_builder.postproc.chunker import chunk_text
    text = "This is a sentence. " * 100  # 2000 chars, single paragraph
    chunks = chunk_text(text, max_chars=200, overlap=0)
    assert len(chunks) > 1
    # With overlap=0, each chunk should be <= max_chars
    for c in chunks:
        assert len(c) <= 200, f"Chunk len {len(c)} > 200: {c[:50]!r}..."


def test_chunk_record_adds_metadata():
    """chunk_record should add chunk_index and total_chunks."""
    from corpus_builder.postproc.chunker import chunk_record
    record = {"content": "Sentence one. " * 500, "source_url": "http://example.com"}
    chunks = chunk_record(record, max_chars=200)
    assert len(chunks) > 1
    for i, c in enumerate(chunks):
        assert c["chunk_index"] == i
        assert c["total_chunks"] == len(chunks)
        assert c["source_url"] == "http://example.com"  # metadata preserved


# ============================================================
# Bug 12: Per-stage count logging (smoke test of pipeline)
# ============================================================

def test_filter_and_dedup_combined_stats():
    """filter_and_dedup_pairs should return both filter and dedup stats."""
    from corpus_builder.postproc.quality_finetune import filter_and_dedup_pairs
    pairs = [
        {"prompt": "What is electronics and circuits?",
         "completion": "Electronics is the study of circuits and devices.",
         "task_type": "qa"},
        {"prompt": "What is electronics and circuits?",
         "completion": "Electronics is the study of circuits and devices.",
         "task_type": "qa"},
        {"prompt": "Hi",
         "completion": "Long answer about electronics here.",
         "task_type": "qa"},
    ]
    result, stats = filter_and_dedup_pairs(pairs)
    assert "filter" in stats
    assert "dedup" in stats
    assert stats["total_kept"] == 1
    assert stats["total_removed"] == 2  # 1 filtered (too short) + 1 deduped



# =====================================================
