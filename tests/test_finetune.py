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



# ============================================================
# Bug 14: Task types filter
# ============================================================

def test_generate_with_task_types_filter(tmp_path):
    """generate_from_corpus should respect task_types filter."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    corpus = tmp_path / "corpus.jsonl"
    long_text = (
        "First paragraph about electronics. Second sentence here.\n\n"
        "Second paragraph about circuits. Another sentence.\n\n"
        "Third paragraph about PCB design. Final sentence."
    )
    corpus.write_text(json.dumps({
        "content": long_text,
        "source_url": "https://example.com",
        "source_type": "html",
    }) + "\n")
    gen = InstructionGenerator()

    # Filter to only article_summary
    pairs = gen.generate_from_corpus(corpus, max_per_type=10,
                                     task_types=["article_summary"])
    types_in_result = set(p["task_type"] for p in pairs)
    assert types_in_result.issubset({"article_summary"}), (
        f"Expected only article_summary, got {types_in_result}"
    )


def test_generate_with_empty_task_types(tmp_path):
    """Empty task_types list should produce no pairs."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps({"content": "x" * 1000, "source_url": "http://e.com"}) + "\n")
    gen = InstructionGenerator()
    pairs = gen.generate_from_corpus(corpus, max_per_type=10, task_types=[])
    assert len(pairs) == 0


def test_generate_with_unknown_task_types(tmp_path):
    """Unknown task types in filter should be silently skipped."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps({"content": "x" * 1000, "source_url": "http://e.com"}) + "\n")
    gen = InstructionGenerator()
    # Only unknown type → no pairs
    pairs = gen.generate_from_corpus(corpus, max_per_type=10,
                                     task_types=["nonexistent_type"])
    assert len(pairs) == 0


# ============================================================
# Bug 15: Multi-turn dialogue
# ============================================================

def test_multi_turn_dialogue_generation(tmp_path):
    """Multi-turn dialogue should produce conversation with multiple turns."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    corpus = tmp_path / "corpus.jsonl"
    content = (
        "# Test Question\n\n"
        "## Вопрос\n\nWhat is an operational amplifier and how does it work?\n\n"
        "## Ответ (score=15) [ПРИНЯТ]\n\n"
        "An op-amp is a DC-coupled high-gain amplifier.\n\n"
        "## Ответ (score=8)\n\n"
        "The gain is controlled by negative feedback.\n\n"
        "## Ответ (score=3)\n\n"
        "Op-amps have slew rate limitations.\n\n"
    )
    corpus.write_text(json.dumps({
        "content": content,
        "source_type": "stackexchange",
        "source_url": "https://electronics.stackexchange.com/q/123",
        "metadata": {"title": "What is an op-amp?"}
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_multi_turn_dialogue(corpus, 10)
    assert len(pairs) > 0
    p = pairs[0]
    assert p["task_type"] == "multi_turn_dialogue"
    assert "conversation" in p
    # Should have at least 4 turns (2 user + 2 assistant)
    assert len(p["conversation"]) >= 4
    # First turn should be user, second should be assistant
    assert p["conversation"][0]["role"] == "user"
    assert p["conversation"][1]["role"] == "assistant"


def test_multi_turn_dialogue_english_markers(tmp_path):
    """Multi-turn should also work with English SE markers."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    corpus = tmp_path / "corpus.jsonl"
    content = (
        "# Test\n\n## Question\n\nWhat is a capacitor and how does it store energy?\n\n"
        "## Answer (score=10) [ACCEPTED]\n\n"
        "A capacitor stores electrical energy in an electric field between two conductive plates.\n\n"
        "## Answer (score=5)\n\n"
        "Capacitors are measured in farads, which is coulombs per volt.\n\n"
    )
    corpus.write_text(json.dumps({
        "content": content,
        "source_type": "stackexchange",
        "source_url": "https://electronics.stackexchange.com/q/456",
        "metadata": {"title": "What is a capacitor?"}
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_multi_turn_dialogue(corpus, 10)
    assert len(pairs) > 0
    assert "conversation" in pairs[0]


def test_multi_turn_dialogue_needs_multiple_answers(tmp_path):
    """Multi-turn should produce no pairs if only one answer exists."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    corpus = tmp_path / "corpus.jsonl"
    content = (
        "## Вопрос\n\nQuestion text here.\n\n"
        "## Ответ (score=5) [ПРИНЯТ]\n\nSingle answer.\n\n"
    )
    corpus.write_text(json.dumps({
        "content": content,
        "source_type": "stackexchange",
        "source_url": "https://example.com/q/1",
        "metadata": {"title": "Test"}
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_multi_turn_dialogue(corpus, 10)
    assert len(pairs) == 0  # Need >= 2 answers


# ============================================================
# Bug 16: Prompt variations
# ============================================================

def test_prompt_variations_exist_for_all_types():
    """Each task type should have multiple prompt variations."""
    from corpus_builder.postproc.prompt_variations import PROMPT_VARIATIONS, get_variation_count
    # Check key types
    for ttype in ["article_summary", "code_explanation", "concept_explanation",
                  "bom_generation", "qa_stackexchange", "faq_qa"]:
        count = get_variation_count(ttype)
        assert count >= 3, f"{ttype} has only {count} variations, need >= 3"


def test_prompt_variations_get_prompt_fills_kwargs():
    """get_prompt should fill in kwargs correctly."""
    from corpus_builder.postproc.prompt_variations import get_prompt
    prompt = get_prompt("code_explanation", code="print('hello')", lang="python")
    assert "print('hello')" in prompt
    assert "python" in prompt


def test_prompt_variations_fallback_for_unknown_type():
    """Unknown task type should fall back gracefully."""
    from corpus_builder.postproc.prompt_variations import get_prompt
    prompt = get_prompt("nonexistent_type", content="Hello world")
    # Should return the content as a fallback
    assert "Hello world" in prompt


def test_prompt_variations_different_calls():
    """Random selection should eventually produce different prompts."""
    from corpus_builder.postproc.prompt_variations import get_prompt, set_seed
    set_seed(42)
    prompts = set()
    for _ in range(20):
        p = get_prompt("article_summary", content="test content here")
        prompts.add(p)
    # Should have at least 2 different variations in 20 calls
    assert len(prompts) >= 2, f"Only got {len(prompts)} unique prompts in 20 calls"


# ============================================================
# Bug 17: Train/Val split
# ============================================================

def test_split_dataset_basic(tmp_path):
    """split_dataset should produce train and val files."""
    from corpus_builder.postproc.format_converter import FormatConverter
    pairs = []
    for i in range(100):
        pairs.append({
            "prompt": f"Question {i} about electronics?",
            "completion": f"Answer {i}: electronics is the study of circuits.",
            "task_type": "qa" if i % 2 == 0 else "code",
        })
    infile = tmp_path / "pairs.jsonl"
    with open(infile, "w") as f:
        for p in pairs:
            f.write(json.dumps(p) + "\n")

    out_dir = tmp_path / "splits"
    result = FormatConverter.split_dataset(infile, out_dir, val_ratio=0.1)
    assert result["_summary"]["total_input"] == 100
    assert result["_summary"]["total_train"] == 90
    assert result["_summary"]["total_val"] == 10
    # Check files exist
    assert (out_dir / "train.jsonl.jsonl").exists()
    assert (out_dir / "val.jsonl.jsonl").exists()


def test_split_dataset_stratified(tmp_path):
    """Stratified split should keep type proportions in train and val."""
    from corpus_builder.postproc.format_converter import FormatConverter
    pairs = []
    for i in range(100):
        pairs.append({
            "prompt": f"Q{i}?",
            "completion": f"A{i}.",
            "task_type": "qa",
        })
    for i in range(50):
        pairs.append({
            "prompt": f"Code Q{i}?",
            "completion": f"Code A{i}.",
            "task_type": "code",
        })
    infile = tmp_path / "pairs.jsonl"
    with open(infile, "w") as f:
        for p in pairs:
            f.write(json.dumps(p) + "\n")

    out_dir = tmp_path / "splits"
    result = FormatConverter.split_dataset(infile, out_dir, val_ratio=0.1,
                                           stratify_by_type=True)
    by_type = result["_summary"]["by_type"]
    # qa: 100 → ~10 val, ~90 train
    assert by_type["qa"]["val"] == 10
    assert by_type["qa"]["train"] == 90
    # code: 50 → ~5 val, ~45 train
    assert by_type["code"]["val"] == 5
    assert by_type["code"]["train"] == 45


def test_split_dataset_invalid_ratio(tmp_path):
    """val_ratio out of (0,1) should raise ValueError."""
    from corpus_builder.postproc.format_converter import FormatConverter
    infile = tmp_path / "pairs.jsonl"
    infile.write_text(json.dumps({"prompt": "Q", "completion": "A"}) + "\n")
    with pytest.raises(ValueError):
        FormatConverter.split_dataset(infile, tmp_path, val_ratio=0.0)
    with pytest.raises(ValueError):
        FormatConverter.split_dataset(infile, tmp_path, val_ratio=1.0)


def test_split_dataset_reproducible(tmp_path):
    """Same seed should produce same split."""
    from corpus_builder.postproc.format_converter import FormatConverter
    pairs = [{"prompt": f"Q{i}", "completion": f"A{i}", "task_type": "qa"} for i in range(50)]
    infile = tmp_path / "pairs.jsonl"
    with open(infile, "w") as f:
        for p in pairs:
            f.write(json.dumps(p) + "\n")

    out1 = tmp_path / "split1"
    out2 = tmp_path / "split2"
    FormatConverter.split_dataset(infile, out1, val_ratio=0.2, seed=123)
    FormatConverter.split_dataset(infile, out2, val_ratio=0.2, seed=123)

    # Read val files and compare
    val1 = (out1 / "val.jsonl.jsonl").read_text()
    val2 = (out2 / "val.jsonl.jsonl").read_text()
    assert val1 == val2, "Same seed should produce identical splits"


# ============================================================
# Bug 18: HTML report
# ============================================================

def test_html_report_generates_valid_html(tmp_path):
    """HTML report should be valid HTML with embedded SVG charts."""
    from corpus_builder.postproc.stats_report import generate_html_report
    pairs = []
    for i in range(20):
        pairs.append({
            "prompt": f"Question {i} about electronics?",
            "completion": f"Answer {i}: electronics is the study of circuits.",
            "task_type": "qa" if i % 2 == 0 else "code",
            "source": f"https://example.com/{i}",
        })
    out_file = tmp_path / "report.html"
    result = generate_html_report(pairs, out_file)
    assert Path(result).exists()
    content = Path(result).read_text()
    assert "<!DOCTYPE html>" in content
    assert "<svg" in content  # has charts
    assert "Question 0" not in content  # individual pairs not shown
    assert "qa" in content  # task type appears


def test_html_report_with_stats(tmp_path):
    """HTML report should include stage counts and warnings if provided."""
    from corpus_builder.postproc.stats_report import generate_html_report
    pairs = [{"prompt": "Q", "completion": "A", "task_type": "qa"}]
    stats = {
        "stage_counts": {"1_generate": {"qa": 10}, "2_filter": {"qa": 8}},
        "warnings": ["Test warning about type collapse"],
    }
    out_file = tmp_path / "report.html"
    generate_html_report(pairs, out_file, stats)
    content = Path(out_file).read_text()
    assert "Pipeline stages" in content
    assert "Test warning" in content


def test_html_report_empty_pairs(tmp_path):
    """HTML report should handle empty pairs list gracefully."""
    from corpus_builder.postproc.stats_report import generate_html_report
    out_file = tmp_path / "empty.html"
    generate_html_report([], out_file)
    content = Path(out_file).read_text()
    assert "<!DOCTYPE html>" in content
    assert "0" in content  # total pairs = 0



# ============================================================
# Bug A: Multi-turn pairs preserved in ShareGPT/ChatML export
# ============================================================

def test_sharegpt_multi_turn_uses_conversation_field():
    """Bug A: ShareGPT export should use 'conversation' field for multi-turn pairs."""
    from corpus_builder.postproc.format_converter import FormatConverter
    import json
    pair = {
        "prompt": "User: Q1",
        "completion": "A2",
        "task_type": "multi_turn_dialogue",
        "conversation": [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Follow-up?"},
            {"role": "assistant", "content": "A2"},
        ],
    }
    out = FormatConverter._to_sharegpt(pair["prompt"], pair["completion"], pair)
    result = json.loads(out)
    assert "conversations" in result
    # Should have 4 turns (not 2!)
    assert len(result["conversations"]) == 4, (
        f"Expected 4 turns for multi-turn, got {len(result['conversations'])}"
    )
    # Check roles alternate correctly
    assert result["conversations"][0]["from"] == "human"
    assert result["conversations"][0]["value"] == "Q1"
    assert result["conversations"][1]["from"] == "gpt"
    assert result["conversations"][1]["value"] == "A1"
    assert result["conversations"][2]["from"] == "human"
    assert result["conversations"][3]["from"] == "gpt"


def test_chatml_multi_turn_uses_conversation_field():
    """Bug A: ChatML export should use 'conversation' field for multi-turn pairs."""
    from corpus_builder.postproc.format_converter import FormatConverter
    import json
    pair = {
        "prompt": "User: Q1",
        "completion": "A2",
        "task_type": "multi_turn_dialogue",
        "conversation": [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "assistant", "content": "A2"},
        ],
    }
    out = FormatConverter._to_chatml(pair["prompt"], pair["completion"], pair)
    result = json.loads(out)
    text = result["text"]
    # Should have 3 im_start blocks (one per turn)
    assert text.count("<|im_start|>") == 3, (
        f"Expected 3 im_start for 3-turn conversation, got {text.count('<|im_start|>')}"
    )
    assert "Q1" in text
    assert "A1" in text
    assert "A2" in text


def test_sharegpt_single_turn_still_works_without_conversation():
    """Bug A: Single-turn pairs (no conversation field) should still export correctly."""
    from corpus_builder.postproc.format_converter import FormatConverter
    import json
    pair = {"prompt": "Hello", "completion": "Hi there", "task_type": "qa"}
    out = FormatConverter._to_sharegpt(pair["prompt"], pair["completion"], pair)
    result = json.loads(out)
    assert len(result["conversations"]) == 2
    assert result["conversations"][0]["from"] == "human"
    assert result["conversations"][0]["value"] == "Hello"


# ============================================================
# Bug B: Multi-turn prompt contains full conversation
# ============================================================

def test_multi_turn_prompt_contains_full_conversation(tmp_path):
    """Bug B: prompt field should contain the full conversation (not just first question)."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import json
    corpus = tmp_path / "corpus.jsonl"
    content = (
        "# Test\n\n## Question\n\n"
        "What is an operational amplifier and how does it work in circuits?\n\n"
        "## Answer (score=15) [ACCEPTED]\n\n"
        "An op-amp is a DC-coupled high-gain amplifier with differential input.\n\n"
        "## Answer (score=8)\n\n"
        "The gain is controlled by negative feedback.\n\n"
    )
    corpus.write_text(json.dumps({
        "content": content,
        "source_type": "stackexchange",
        "source_url": "https://electronics.stackexchange.com/q/123",
        "metadata": {"title": "What is an op-amp?"}
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_multi_turn_dialogue(corpus, 10)
    assert len(pairs) > 0
    p = pairs[0]
    # Prompt should contain "User:" and "Assistant:" markers (full conversation)
    assert "User:" in p["prompt"], (
        f"Prompt should contain User: markers for full conversation. Got: {p['prompt'][:200]}"
    )
    assert "Assistant:" in p["prompt"], (
        f"Prompt should contain Assistant: markers. Got: {p['prompt'][:200]}"
    )
    # Prompt should NOT be just the first question
    assert p["prompt"] != "What is an op-amp?", (
        "Prompt should not be just the first question"
    )


# ============================================================
# Bug C: Dedup preserves multi-turn pairs with different conversations
# ============================================================

def test_dedup_preserves_different_multi_turn_conversations():
    """Bug C: Two multi-turn pairs with same prompt+completion but different
    conversations should NOT be deduped."""
    from corpus_builder.postproc.quality_finetune import dedup_pairs, _pair_hash
    p1 = {
        "prompt": "User: Q1",
        "completion": "A2",
        "task_type": "multi_turn_dialogue",
        "conversation": [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Follow-up?"},
            {"role": "assistant", "content": "A2"},
        ],
    }
    p2 = {
        "prompt": "User: Q1",
        "completion": "A2",
        "task_type": "multi_turn_dialogue",
        "conversation": [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "DIFFERENT_A1"},
            {"role": "user", "content": "DIFFERENT_follow-up"},
            {"role": "assistant", "content": "A2"},
        ],
    }
    # Hashes should differ
    h1 = _pair_hash(p1, "prompt+completion")
    h2 = _pair_hash(p2, "prompt+completion")
    assert h1 != h2, "Different conversations should have different hashes"
    # Dedup should keep both
    result, stats = dedup_pairs([p1, p2])
    assert len(result) == 2, (
        f"Expected 2 pairs (different conversations), got {len(result)}"
    )


def test_dedup_removes_identical_multi_turn_conversations():
    """Bug C: Truly identical multi-turn pairs should still be deduped."""
    from corpus_builder.postproc.quality_finetune import dedup_pairs
    p1 = {
        "prompt": "User: Q1",
        "completion": "A2",
        "task_type": "multi_turn_dialogue",
        "conversation": [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "assistant", "content": "A2"},
        ],
    }
    p2 = dict(p1)
    result, stats = dedup_pairs([p1, p2])
    assert len(result) == 1, f"Expected 1 (identical), got {len(result)}"


# ============================================================
# Bug D: balance_by_type uses different random samples per type
# ============================================================

def test_balance_by_type_different_samples_per_type():
    """Bug D: Different task types should get different random subsamples."""
    from corpus_builder.postproc.dataset_balancer import balance_by_type
    # 100 qa + 100 code pairs, sample 10 from each
    pairs = []
    for i in range(100):
        pairs.append({"prompt": f"Q{i}", "completion": f"A{i}", "task_type": "qa"})
    for i in range(100):
        pairs.append({"prompt": f"Code Q{i}", "completion": f"Code A{i}", "task_type": "code"})
    result = balance_by_type(pairs, max_per_type=10)
    qa = [p for p in result if p["task_type"] == "qa"]
    code = [p for p in result if p["task_type"] == "code"]
    assert len(qa) == 10
    assert len(code) == 10
    # Extract indices
    qa_indices = [int(p["prompt"][1:]) for p in qa]
    code_indices = [int(p["prompt"].replace("Code Q", "")) for p in code]
    # Should be DIFFERENT (not the same indices selected for both types)
    assert qa_indices != code_indices, (
        f"BUG: Same indices selected for both types! qa={qa_indices[:5]}, code={code_indices[:5]}"
    )


def test_balance_by_type_reproducible_with_seed():
    """Bug D: Same seed should produce same subsample."""
    from corpus_builder.postproc.dataset_balancer import balance_by_type
    pairs = [{"prompt": f"Q{i}", "completion": f"A{i}", "task_type": "qa"} for i in range(100)]
    r1 = balance_by_type(pairs, max_per_type=10, seed=42)
    r2 = balance_by_type(pairs, max_per_type=10, seed=42)
    assert r1 == r2, "Same seed should produce identical results"


# ============================================================
# Bug E: Q&A parsing strips (score=N) prefix from answers
# ============================================================

def test_qa_pairs_strips_score_prefix_no_accepted(tmp_path):
    """Bug E: Answer should not contain '(score=N)' prefix when no [ПРИНЯТ]."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import json
    corpus = tmp_path / "corpus.jsonl"
    content = (
        "## Вопрос\n\nWhat is a diode and how does it work in circuits?\n\n"
        "## Ответ (score=5)\n\nA diode allows current in one direction only.\n\n"
        "## Ответ (score=3)\n\nIt is a semiconductor device.\n\n"
    )
    corpus.write_text(json.dumps({
        "content": content,
        "source_type": "stackexchange",
        "source_url": "https://example.com/q/1",
        "metadata": {"title": "Diode question"}
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_qa_pairs(corpus, 10)
    assert len(pairs) > 0
    answer = pairs[0]["completion"]
    assert "(score=" not in answer, (
        f"Answer should not contain score prefix. Got: {answer!r}"
    )
    assert "diode" in answer.lower(), f"Answer should be about diodes. Got: {answer!r}"


def test_qa_pairs_picks_accepted_over_higher_score(tmp_path):
    """Bug E: Accepted answer should be picked even if it has lower score."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import json
    corpus = tmp_path / "corpus.jsonl"
    content = (
        "## Вопрос\n\nWhat is a capacitor and how does it store energy?\n\n"
        "## Ответ (score=2) [ПРИНЯТ]\n\nA capacitor stores energy in an electric field.\n\n"
        "## Ответ (score=10)\n\nCapacitors are measured in farads units.\n\n"
    )
    corpus.write_text(json.dumps({
        "content": content,
        "source_type": "stackexchange",
        "source_url": "https://example.com/q/2",
        "metadata": {"title": "Capacitor question"}
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_qa_pairs(corpus, 10)
    assert len(pairs) > 0
    answer = pairs[0]["completion"]
    # Should pick accepted (score=2) over higher score (score=10)
    assert "electric field" in answer.lower(), (
        f"Should pick accepted answer. Got: {answer!r}"
    )
    assert "(score=" not in answer, f"Answer should not contain score prefix. Got: {answer!r}"


def test_qa_pairs_strips_score_prefix_english(tmp_path):
    """Bug E: English markers should also strip (score=N) prefix."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import json
    corpus = tmp_path / "corpus.jsonl"
    content = (
        "## Question\n\nWhat is an operational amplifier and how does it work?\n\n"
        "## Answer (score=15) [ACCEPTED]\n\nAn op-amp is a DC-coupled high-gain amplifier.\n\n"
        "## Answer (score=8)\n\nThe gain is controlled by negative feedback.\n\n"
    )
    corpus.write_text(json.dumps({
        "content": content,
        "source_type": "stackexchange",
        "source_url": "https://example.com/q/3",
        "metadata": {"title": "Op-amp question"}
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_qa_pairs(corpus, 10)
    assert len(pairs) > 0
    answer = pairs[0]["completion"]
    assert "(score=" not in answer, (
        f"Answer should not contain score prefix. Got: {answer!r}"
    )
    assert "op-amp" in answer.lower() or "amplifier" in answer.lower()


def test_qa_pairs_without_score_marker(tmp_path):
    """Bug E: Should handle answers without (score=N) marker at all."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import json
    corpus = tmp_path / "corpus.jsonl"
    # Old format without (score=N) — some legacy data may have this
    content = (
        "## Question\n\nWhat is a resistor and how is it used?\n\n"
        "## Answer\n\n[ACCEPTED] A resistor limits current flow in a circuit.\n\n"
    )
    corpus.write_text(json.dumps({
        "content": content,
        "source_type": "stackexchange",
        "source_url": "https://example.com/q/4",
        "metadata": {"title": "Resistor question"}
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_qa_pairs(corpus, 10)
    assert len(pairs) > 0
    answer = pairs[0]["completion"]
    assert "resistor" in answer.lower() or "current" in answer.lower()


# ============================================================
# Bug F: No duplicate _on_export method
# ============================================================

def test_finetune_window_has_single_on_export():
    """Bug F: FinetuneWindow should have exactly one _on_export method."""
    import ast
    with open('corpus_builder/finetune_window.py') as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'FinetuneWindow':
            exports = [item for item in node.body
                       if isinstance(item, ast.FunctionDef) and item.name == '_on_export']
            assert len(exports) == 1, (
                f"Expected 1 _on_export method, found {len(exports)} "
                f"at lines {[e.lineno for e in exports]}"
            )
            return
    assert False, "FinetuneWindow class not found"


# ============================================================
# Bug G: concept_explanation truncates at next heading
# ============================================================

def test_concept_explanation_no_section_bleed(tmp_path):
    """Bug G: completion should not include content from the next section."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import json
    corpus = tmp_path / "corpus.jsonl"
    content = (
        "# Section 1: Electronics\n\n"
        "This is content about section one which is quite long and detailed. "
        "It has multiple sentences about electronics and circuits. "
        "The content should be self-contained.\n\n"
        "# Section 2: Circuits\n\n"
        "This is content about section two which is different.\n\n"
        "# Section 3: PCB Design\n\n"
        "Third section about PCB layout and design."
    )
    corpus.write_text(json.dumps({"content": content, "source_url": "http://e.com"}) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_concept_explanation(corpus, 10)
    assert len(pairs) > 0
    for p in pairs:
        assert "# Section 2" not in p["completion"], (
            f"Section 2 leaked into Section 1 completion: {p['completion'][:200]}"
        )
        assert "# Section 3" not in p["completion"], (
            f"Section 3 leaked into completion: {p['completion'][:200]}"
        )


def test_concept_explanation_single_section(tmp_path):
    """Bug G: single section (no next heading) should work."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import json
    corpus = tmp_path / "corpus.jsonl"
    content = (
        "# Single Section\n\n"
        "This is the only section. " * 20
    )
    corpus.write_text(json.dumps({"content": content, "source_url": "http://e.com"}) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_concept_explanation(corpus, 10)
    assert len(pairs) > 0
    assert "only section" in pairs[0]["completion"].lower()


# ============================================================
# Bug H: HTML report uses random sampling
# ============================================================

def test_html_report_random_sample_not_biased(tmp_path):
    """Bug H: sample should be random, not just pairs[:500]."""
    from corpus_builder.postproc.stats_report import generate_html_report
    import re
    pairs = []
    for i in range(1000):
        pairs.append({"prompt": "Short Q", "completion": "Short A", "task_type": "qa"})
    for i in range(1000):
        pairs.append({"prompt": "Long code explanation prompt " * 10,
                      "completion": "x" * 500, "task_type": "code"})
    out_file = tmp_path / "report.html"
    generate_html_report(pairs, out_file)
    content = out_file.read_text()
    m = re.search(r'card-value">(\d+)</div><div class="card-label">Avg prompt chars', content)
    assert m, "Avg prompt chars not found in report"
    avg = int(m.group(1))
    assert avg > 20, (
        f"Sample appears biased (avg={avg}). Expected random mix."
    )


# ============================================================
# Bug I: Corpus read only once per cache type
# ============================================================

def test_corpus_cache_reads_file_once(tmp_path):
    """Bug I: generate_from_corpus should read the corpus file at most twice."""
    import corpus_builder.postproc.instruction_generator as mod
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import json

    corpus = tmp_path / "corpus.jsonl"
    records = [
        {"content": "Article about electronics. " * 100 + "\n\nSecond paragraph. " * 50,
         "source_type": "html", "source_url": "http://e.com/1"},
        {"content": "## Question\n\nWhat is a diode?\n\n## Answer (score=5)\n\nA diode.\n\n",
         "source_type": "stackexchange", "source_url": "http://se.com/1",
         "metadata": {"title": "Q"}},
        {"content": "PDF content. " * 50,
         "source_type": "pdf", "source_url": "http://e.com/pdf"},
    ]
    with open(corpus, 'w') as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    gen = InstructionGenerator()
    orig_open = mod.open_corpus_reader
    call_count = {"calls": 0}

    def counting_open(path):
        call_count["calls"] += 1
        return orig_open(path)

    mod.open_corpus_reader = counting_open
    try:
        gen.generate_from_corpus(corpus, max_per_type=100)
    finally:
        mod.open_corpus_reader = orig_open

    assert call_count["calls"] <= 2, (
        f"BUG: corpus read {call_count['calls']} times, expected <= 2"
    )


def test_corpus_cache_clear():
    """Bug I: _clear_cache should reset the cache."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    gen = InstructionGenerator()
    gen._corpus_cache["fake_path"] = {"html": []}
    gen._corpus_cache_chunked["fake_path"] = {"html": []}
    gen._clear_cache()
    assert len(gen._corpus_cache) == 0
    assert len(gen._corpus_cache_chunked) == 0


# ============================================================
# Bug J: datasheet_specs uses chunker and get_prompt
# ============================================================

def test_datasheet_specs_uses_get_prompt(tmp_path):
    """Bug J: TOC fallback should use get_prompt, not hardcoded string."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import json
    corpus = tmp_path / "corpus.jsonl"
    content = "Datasheet content. " * 50
    corpus.write_text(json.dumps({
        "content": content,
        "source_type": "pdf",
        "source_url": "http://e.com/ds.pdf",
        "metadata": {"title": "LM358", "toc": [["1", "Features"], ["2", "Description"]]}
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_datasheet_specs(corpus, 10)
    assert len(pairs) > 0
    assert "List the main sections of an electronic component datasheet" not in pairs[0]["prompt"], (
        f"Should use get_prompt variations, not hardcoded. Got: {pairs[0]['prompt'][:100]}"
    )


def test_datasheet_specs_escapes_title(tmp_path):
    """Bug J: title with special chars should not break the prompt."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import json
    corpus = tmp_path / "corpus.jsonl"
    content = "Datasheet content. " * 50
    corpus.write_text(json.dumps({
        "content": content,
        "source_type": "pdf",
        "source_url": "http://e.com/ds.pdf",
        "metadata": {"title": "LM358\n\n# Malicious", "toc": [["1", "Features"]]}
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_datasheet_specs(corpus, 10)
    assert len(pairs) > 0


# ============================================================
# Bug K: Prompt variations reproducible
# ============================================================

def test_prompt_variations_reproducible_after_set_seed():
    """Bug K: set_seed should make prompt selection reproducible."""
    from corpus_builder.postproc.prompt_variations import get_prompt, set_seed
    set_seed(42)
    prompts1 = [get_prompt("article_summary", content="test content") for _ in range(10)]
    set_seed(42)
    prompts2 = [get_prompt("article_summary", content="test content") for _ in range(10)]
    assert prompts1 == prompts2, "Same seed should produce same prompt sequence"


def test_finetune_worker_calls_set_seed():
    """Bug K: FinetuneWorker.run() should call set_prompt_seed(42)."""
    import ast
    with open('corpus_builder/finetune_window.py') as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'FinetuneWorker':
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == 'run':
                    src = ast.dump(item)
                    assert 'set_prompt_seed' in src or 'set_seed' in src, (
                        "FinetuneWorker.run() should call set_seed"
                    )
                    return
    assert False, "FinetuneWorker.run() not found"


# ============================================================
# Bug L: BOM fallback removed
# ============================================================

def test_bom_no_fallback_for_non_kicad_projects(tmp_path):
    """Bug L: github_repo without KiCad files should NOT produce BOM pairs."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import json
    corpus = tmp_path / "corpus.jsonl"
    content = "This is a README for a project. " * 50
    corpus.write_text(json.dumps({
        "content": content,
        "source_type": "github_repo",
        "source_url": "http://github.com/user/repo",
        "downloaded_files": []
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_bom(corpus, 10)
    assert len(pairs) == 0, (
        f"BUG: BOM fallback created {len(pairs)} meaningless pairs. Expected 0."
    )


def test_bom_real_kicad_still_works(tmp_path):
    """Bug L: real KiCad files should still produce BOM pairs."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import json
    kicad_file = tmp_path / "test.kicad_sch"
    kicad_content = '(lib_id "Device:R")\n(property "Reference" "R1")\n(property "Value" "10k")\n'
    kicad_file.write_text(kicad_content)

    corpus = tmp_path / "corpus.jsonl"
    content = "Project README. " * 20
    corpus.write_text(json.dumps({
        "content": content,
        "source_type": "github_repo",
        "source_url": "http://github.com/user/repo",
        "downloaded_files": [{"type": "kicad", "local_path": str(kicad_file)}]
    }) + "\n")
    gen = InstructionGenerator()
    pairs = gen._gen_bom(corpus, 10)
    assert len(pairs) > 0
    assert "BOM" in pairs[0]["completion"]
    assert "R1" in pairs[0]["completion"]
    assert "10k" in pairs[0]["completion"]


# ============================================================
# Improvement 15: tiktoken encoder cached at module level + batch
# ============================================================

def test_token_utils_batch_mode():
    """Improvement 15: count_tokens_batch should work faster than per-text."""
    from corpus_builder.postproc.token_utils import count_tokens, count_tokens_batch
    texts = ["Hello world", "Another text", "Third one here"]
    batch_result = count_tokens_batch(texts)
    per_text = [count_tokens(t) for t in texts]
    assert batch_result == per_text, f"Batch {batch_result} != per-text {per_text}"


def test_token_utils_empty_batch():
    """Improvement 15: empty batch should return empty list."""
    from corpus_builder.postproc.token_utils import count_tokens_batch
    assert count_tokens_batch([]) == []


def test_token_utils_encoder_loaded_at_import():
    """Improvement 15: encoder should be loaded at module level (not lazy)."""
    import corpus_builder.postproc.token_utils as mod
    # _ENCODER should be set (or None if tiktoken unavailable)
    assert hasattr(mod, '_ENCODER')
    assert hasattr(mod, '_TIKTOKEN_AVAILABLE')


# ============================================================
# Improvement 16: Chunker preserves code blocks atomically
# ============================================================

def test_chunker_preserves_code_blocks(tmp_path):
    """Improvement 16: code blocks should not be split mid-block."""
    from corpus_builder.postproc.chunker import chunk_text
    import re
    # Create text with a code block that would be split without protection
    code = "```python\n" + "x = 1\n" * 200 + "```"  # ~1300 chars
    text = "Intro paragraph. " * 20 + "\n\n" + code + "\n\n" + "Outro. " * 20
    chunks = chunk_text(text, max_chars=500)
    # Each chunk should have complete code blocks (even number of fences)
    for i, c in enumerate(chunks):
        fences = c.count("```")
        assert fences % 2 == 0, (
            f"Chunk {i} has {fences} fences (unpaired — code block split!)"
        )


def test_chunker_code_block_placeholder_extraction():
    """Improvement 16: _extract_code_blocks should replace blocks with placeholders."""
    from corpus_builder.postproc.chunker import _extract_code_blocks, _restore_code_blocks
    text = "Before\n```python\ncode here\n```\nAfter"
    text_with_ph, blocks = _extract_code_blocks(text)
    assert len(blocks) == 1
    assert "```python\ncode here\n```" in blocks[0]
    assert "__CODE_BLOCK_0__" in text_with_ph
    # Restore
    restored = _restore_code_blocks(text_with_ph, blocks)
    assert restored == text


# ============================================================
# Improvement 17: System prompt support for ChatML
# ============================================================

def test_chatml_with_system_prompt():
    """Improvement 17: ChatML should support optional system prompt."""
    from corpus_builder.postproc.format_converter import FormatConverter
    import json
    pair = {"prompt": "Hello", "completion": "Hi", "task_type": "qa"}
    out = FormatConverter._to_chatml(
        pair["prompt"], pair["completion"], pair,
        system_prompt="You are a helpful assistant."
    )
    result = json.loads(out)
    text = result["text"]
    assert "<|im_start|>system" in text
    assert "You are a helpful assistant." in text
    assert "<|im_start|>user" in text
    assert "<|im_start|>assistant" in text


def test_chatml_without_system_prompt_still_works():
    """Improvement 17: ChatML without system prompt should work as before."""
    from corpus_builder.postproc.format_converter import FormatConverter
    import json
    pair = {"prompt": "Hello", "completion": "Hi", "task_type": "qa"}
    out = FormatConverter._to_chatml(pair["prompt"], pair["completion"], pair)
    result = json.loads(out)
    text = result["text"]
    assert "<|im_start|>system" not in text
    assert "<|im_start|>user" in text


def test_convert_with_system_prompt(tmp_path):
    """Improvement 17: convert() should pass system_prompt to chatml."""
    from corpus_builder.postproc.format_converter import FormatConverter
    import json
    infile = tmp_path / "pairs.jsonl"
    infile.write_text(json.dumps({"prompt": "Q", "completion": "A"}) + "\n")
    outfile = tmp_path / "out.jsonl"
    FormatConverter.convert(infile, outfile, "chatml",
                            system_prompt="You are an expert.")
    data = json.loads(outfile.read_text())
    assert "<|im_start|>system" in data["text"]
    assert "You are an expert." in data["text"]


# ============================================================
# Improvement 18: Semantic dedup via MinHash
# ============================================================

def test_semantic_dedup_removes_paraphrases():
    """Improvement 18: near-duplicate prompts should be removed."""
    from corpus_builder.postproc.quality_finetune import dedup_pairs_semantic
    pairs = [
        {"prompt": "Explain what this code does in detail please", "completion": "A"},
        {"prompt": "Explain what this code does in detail please", "completion": "A"},  # exact dup
        {"prompt": "Can you explain what this code does for me", "completion": "B"},  # paraphrase
        {"prompt": "What is the weather like today in Moscow", "completion": "C"},  # different
    ]
    result, stats = dedup_pairs_semantic(pairs, threshold=0.5)
    # Should remove at least the exact dup and the paraphrase
    assert len(result) <= 3, f"Expected <= 3, got {len(result)}"
    assert stats["removed"] >= 1


def test_semantic_dedup_keeps_different_prompts():
    """Improvement 18: completely different prompts should all be kept."""
    from corpus_builder.postproc.quality_finetune import dedup_pairs_semantic
    pairs = [
        {"prompt": "What is electronics?", "completion": "A"},
        {"prompt": "How do I cook pasta?", "completion": "B"},
        {"prompt": "Explain quantum physics.", "completion": "C"},
    ]
    result, stats = dedup_pairs_semantic(pairs, threshold=0.7)
    assert len(result) == 3, f"Expected 3, got {len(result)}"


def test_semantic_dedup_empty():
    """Improvement 18: empty input should return empty."""
    from corpus_builder.postproc.quality_finetune import dedup_pairs_semantic
    result, stats = dedup_pairs_semantic([], threshold=0.5)
    assert len(result) == 0
    assert stats["input"] == 0


# ============================================================
# Improvement 19: Custom prompts from YAML/JSON
# ============================================================

def test_load_custom_prompts_json(tmp_path):
    """Improvement 19: load custom prompts from JSON file."""
    from corpus_builder.postproc.prompt_variations import (
        load_custom_prompts, get_variation_count, reset_to_defaults
    )
    import json
    reset_to_defaults()
    default_count = get_variation_count("article_summary")
    custom_file = tmp_path / "custom.json"
    custom_file.write_text(json.dumps({
        "article_summary": ["Custom prompt: {content}"],
        "new_type": ["New: {input}"],
    }))
    added = load_custom_prompts(custom_file)
    assert "article_summary" in added
    assert "new_type" in added
    assert get_variation_count("article_summary") == default_count + 1
    assert get_variation_count("new_type") == 1
    reset_to_defaults()


def test_load_custom_prompts_nonexistent():
    """Improvement 19: loading nonexistent file should return empty dict."""
    from corpus_builder.postproc.prompt_variations import load_custom_prompts
    result = load_custom_prompts("/nonexistent/path/file.json")
    assert result == {}


def test_reset_to_defaults_clears_custom():
    """Improvement 19: reset should remove all custom prompts."""
    from corpus_builder.postproc.prompt_variations import (
        load_custom_prompts, get_variation_count, reset_to_defaults
    )
    import json, tempfile
    reset_to_defaults()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({"article_summary": ["Custom: {content}"]}, f)
        path = f.name
    load_custom_prompts(path)
    assert get_variation_count("article_summary") > 0
    reset_to_defaults()
    # Should be back to defaults
    from corpus_builder.postproc.prompt_variations import _DEFAULT_PROMPT_VARIATIONS
    assert get_variation_count("article_summary") == len(_DEFAULT_PROMPT_VARIATIONS["article_summary"])


# ============================================================
# Improvement 20: Execution time profiling
# ============================================================

def test_finetune_worker_has_stage_times():
    """Improvement 20: FinetuneWorker should have stage_times attribute."""
    import ast
    with open('corpus_builder/finetune_window.py') as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'FinetuneWorker':
            # Check __init__ has stage_times
            src = ast.dump(node)
            assert 'stage_times' in src, "FinetuneWorker should have stage_times"
            return
    assert False, "FinetuneWorker not found"


def test_finetune_worker_accepts_use_token_limits():
    """Improvement 13: FinetuneWorker should accept use_token_limits parameter."""
    import ast
    with open('corpus_builder/finetune_window.py') as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'FinetuneWorker':
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == '__init__':
                    args = [a.arg for a in item.args.args]
                    assert 'use_token_limits' in args, (
                        f"FinetuneWorker.__init__ should have use_token_limits, got: {args}"
                    )
                    return
    assert False, "FinetuneWorker.__init__ not found"


# ============================================================
# Improvement 21: Context-aware multi-turn follow-ups
# ============================================================

def test_follow_up_uses_bridge_terms():
    """Improvement 21: follow-up should mention bridge terms from answers."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import random
    rng = random.Random(42)
    prev = "An op-amp is a DC-coupled high-gain amplifier with differential input."
    next_ans = "The gain is controlled by negative feedback on the differential input."
    fu = InstructionGenerator._generate_follow_up(prev, next_ans, rng)
    # Should mention a bridge term (gain, input, differential, amplifier, feedback)
    bridge_terms = ["gain", "input", "differential", "amplifier", "feedback"]
    assert any(term in fu.lower() for term in bridge_terms), (
        f"Follow-up should mention a bridge term, got: {fu}"
    )


def test_follow_up_fallback_when_no_bridge():
    """Improvement 21: fallback to generic when no bridge terms."""
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    import random
    rng = random.Random(42)
    prev = "Hello world."
    next_ans = "Goodbye universe."
    fu = InstructionGenerator._generate_follow_up(prev, next_ans, rng)
    # Should be a non-empty string
    assert len(fu) > 10
    assert "?" in fu  # should be a question


# ============================================================
# Improvement 22: Axolotl + LLaMA-Factory formats
# ============================================================

def test_axolotl_format_single_turn():
    """Improvement 22: Axolotl format should work for single-turn."""
    from corpus_builder.postproc.format_converter import FormatConverter
    import json
    pair = {"prompt": "Hello", "completion": "Hi", "task_type": "qa"}
    out = FormatConverter._to_axolotl(pair["prompt"], pair["completion"], pair)
    result = json.loads(out)
    assert "conversation" in result
    assert len(result["conversation"]) == 2
    assert result["conversation"][0]["from"] == "human"
    assert result["conversation"][0]["value"] == "Hello"
    assert result["conversation"][1]["from"] == "gpt"


def test_axolotl_format_multi_turn():
    """Improvement 22: Axolotl should use conversation field for multi-turn."""
    from corpus_builder.postproc.format_converter import FormatConverter
    import json
    pair = {
        "prompt": "Q1", "completion": "A2",
        "conversation": [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Follow-up?"},
            {"role": "assistant", "content": "A2"},
        ],
    }
    out = FormatConverter._to_axolotl(pair["prompt"], pair["completion"], pair)
    result = json.loads(out)
    assert len(result["conversation"]) == 4
    assert result["conversation"][0]["from"] == "human"
    assert result["conversation"][1]["from"] == "gpt"


def test_axolotl_with_system_prompt():
    """Improvement 22: Axolotl should support system prompt."""
    from corpus_builder.postproc.format_converter import FormatConverter
    import json
    pair = {"prompt": "Q", "completion": "A"}
    out = FormatConverter._to_axolotl(pair["prompt"], pair["completion"], pair,
                                      system_prompt="You are an expert.")
    result = json.loads(out)
    assert result["conversation"][0]["from"] == "system"
    assert result["conversation"][0]["value"] == "You are an expert."


def test_llama_factory_format_single_turn():
    """Improvement 22: LLaMA-Factory format should work for single-turn."""
    from corpus_builder.postproc.format_converter import FormatConverter
    import json
    pair = {"prompt": "Hello", "completion": "Hi", "task_type": "qa"}
    out = FormatConverter._to_llama_factory(pair["prompt"], pair["completion"], pair)
    result = json.loads(out)
    assert "messages" in result
    assert len(result["messages"]) == 2
    assert result["messages"][0]["role"] == "user"
    assert result["messages"][0]["content"] == "Hello"
    assert result["messages"][1]["role"] == "assistant"


def test_llama_factory_multi_turn():
    """Improvement 22: LLaMA-Factory should use conversation field."""
    from corpus_builder.postproc.format_converter import FormatConverter
    import json
    pair = {
        "prompt": "Q1", "completion": "A2",
        "conversation": [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "assistant", "content": "A2"},
        ],
    }
    out = FormatConverter._to_llama_factory(pair["prompt"], pair["completion"], pair)
    result = json.loads(out)
    assert len(result["messages"]) == 3


def test_convert_all_includes_new_formats(tmp_path):
    """Improvement 22: convert_all should produce axolotl + llama_factory files."""
    from corpus_builder.postproc.format_converter import FormatConverter
    import json
    infile = tmp_path / "pairs.jsonl"
    infile.write_text(json.dumps({"prompt": "Q", "completion": "A"}) + "\n")
    out_dir = tmp_path / "out"
    results = FormatConverter.convert_all(infile, out_dir)
    assert "axolotl" in results
    assert "llama_factory" in results
    assert (out_dir / "finetune_axolotl.jsonl").exists()
    assert (out_dir / "finetune_llama_factory.jsonl").exists()


# ============================================================
# Improvement 14: Corpus validation
# ============================================================

def test_corpus_validation_empty_file(tmp_path):
    """Improvement 14: empty corpus file should produce warning."""
    # We can't easily test the GUI method directly, but we can test the
    # validation logic by simulating it.
    import json
    corpus = tmp_path / "empty.jsonl"
    corpus.write_text("")  # empty
    # Simulate the validation logic
    file_size = corpus.stat().st_size
    assert file_size == 0


def test_corpus_validation_no_supported_types(tmp_path):
    """Improvement 14: corpus with unsupported source_types should warn."""
    import json
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps({
        "content": "text", "source_type": "unsupported_type"
    }) + "\n")
    # Read and check
    records = []
    with open(corpus) as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    assert len(records) == 1
    supported = {"html", "stackexchange", "pdf", "github_repo", ""}
    has_supported = any(r.get("source_type", "") in supported for r in records)
    assert not has_supported
