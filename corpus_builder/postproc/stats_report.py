"""HTML statistics report for fine-tuning datasets.

Generates a self-contained HTML file with:
  - Summary cards (total pairs, num types, avg prompt/completion length)
  - Bar chart of pair counts by task_type (inline SVG, no JS deps)
  - Histogram of prompt/completion lengths (inline SVG)
  - Language distribution (if detectable)
  - Warnings table (if any)
  - Stage-by-stage pipeline counts table

The HTML is fully self-contained — no external CSS/JS, no internet needed.
Opens in any browser.
"""
from __future__ import annotations

import html
from collections import Counter
from datetime import datetime
from pathlib import Path

from ..logging_setup import get_logger
from .token_utils import _detect_lang_simple, count_tokens

log = get_logger(__name__)


def _escape(text: str) -> str:
    """HTML-escape text."""
    return html.escape(str(text), quote=True)


def _bar_chart_svg(data: list[tuple[str, int]], title: str,
                   width: int = 600, height: int = 300,
                   bar_color: str = "#007acc") -> str:
    """Generate an inline SVG bar chart.

    Args:
        data: list of (label, value) pairs
        title: chart title
        width/height: SVG dimensions
        bar_color: hex color for bars
    """
    if not data:
        return f'<p><em>No data for {html.escape(title)}</em></p>'

    max_val = max(v for _, v in data) or 1
    n_bars = len(data)
    chart_w = width - 120  # leave room for labels
    chart_h = height - 60
    bar_w = max(10, min(40, chart_w // max(n_bars, 1) - 8))
    bar_spacing = chart_w / n_bars

    bars = []
    x = 60  # left margin for labels
    for i, (label, val) in enumerate(data):
        bar_h = int((val / max_val) * chart_h)
        y = 30 + (chart_h - bar_h)
        # Truncate long labels
        short_label = label[:12] + ("…" if len(label) > 12 else "")
        bars.append(f'''
        <g>
          <rect x="{x:.0f}" y="{y:.0f}" width="{bar_w}" height="{bar_h}"
                fill="{bar_color}" rx="2">
            <title>{_escape(label)}: {val}</title>
          </rect>
          <text x="{x + bar_w/2:.0f}" y="{height - 25}" text-anchor="middle"
                font-size="10" fill="#666">{_escape(short_label)}</text>
          <text x="{x + bar_w/2:.0f}" y="{y - 5:.0f}" text-anchor="middle"
                font-size="10" fill="#333">{val}</text>
        </g>''')
        x += bar_spacing

    return f'''
    <svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"
         style="background: #fafafa; border-radius: 4px;">
      <text x="{width/2:.0f}" y="15" text-anchor="middle" font-size="13"
            font-weight="bold" fill="#333">{_escape(title)}</text>
      <line x1="60" y1="{height-35}" x2="{width-20}" y2="{height-35}"
            stroke="#ccc" stroke-width="1"/>
      {''.join(bars)}
    </svg>'''


def _histogram_svg(values: list[int], title: str, n_bins: int = 10,
                   width: int = 600, height: int = 200,
                   bar_color: str = "#4ec9b0") -> str:
    """Generate an inline SVG histogram."""
    if not values:
        return f'<p><em>No data for {html.escape(title)}</em></p>'

    min_v, max_v = min(values), max(values)
    if min_v == max_v:
        max_v = min_v + 1
    bin_size = (max_v - min_v) / n_bins
    bins = [0] * n_bins
    for v in values:
        idx = min(int((v - min_v) / bin_size), n_bins - 1)
        bins[idx] += 1

    max_count = max(bins) or 1
    chart_w = width - 80
    chart_h = height - 50
    bar_w = chart_w / n_bins

    bars = []
    for i, count in enumerate(bins):
        bar_h = int((count / max_count) * chart_h)
        x = 40 + i * bar_w
        y = 20 + (chart_h - bar_h)
        bin_start = int(min_v + i * bin_size)
        bin_end = int(min_v + (i + 1) * bin_size)
        bars.append(f'''
        <g>
          <rect x="{x:.0f}" y="{y:.0f}" width="{bar_w-1:.0f}" height="{bar_h}"
                fill="{bar_color}" rx="1">
            <title>{bin_start}-{bin_end}: {count} pairs</title>
          </rect>
        </g>''')

    return f'''
    <svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"
         style="background: #fafafa; border-radius: 4px;">
      <text x="{width/2:.0f}" y="12" text-anchor="middle" font-size="12"
            font-weight="bold" fill="#333">{_escape(title)}</text>
      <text x="10" y="{height-25}" font-size="10" fill="#666">{min_v}</text>
      <text x="{width-30}" y="{height-25}" font-size="10" fill="#666">{max_v}</text>
      <line x1="40" y1="{height-30}" x2="{width-20}" y2="{height-30}"
            stroke="#ccc" stroke-width="1"/>
      {''.join(bars)}
    </svg>'''


def generate_html_report(
    pairs: list[dict],
    output_file: str | Path,
    stats: dict | None = None,
    title: str = "Fine-Tuning Dataset Report",
) -> str:
    """Generate a self-contained HTML report.

    Args:
        pairs: list of instruction pairs
        stats: optional stats dict from FinetuneWorker (with stage_counts, warnings, etc.)
        output_file: path to write HTML
        title: report title

    Returns:
        path to the written HTML file
    """
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Compute statistics
    total = len(pairs)
    by_type: Counter = Counter(p.get("task_type", "unknown") for p in pairs)

    # Length stats (chars and tokens) — sample up to 500 pairs for speed.
    # Use RANDOM sampling (not pairs[:500]) to avoid bias when pairs are
    # sorted by task_type after balance_by_type.
    import random as _random
    if len(pairs) > 500:
        sample = _random.Random(42).sample(pairs, 500)
    else:
        sample = pairs
    prompt_chars = [len(p.get("prompt", "")) for p in sample]
    completion_chars = [len(p.get("completion", "")) for p in sample]
    prompt_tokens = [count_tokens(p.get("prompt", "")) for p in sample]
    completion_tokens = [count_tokens(p.get("completion", "")) for p in sample]

    avg_prompt_chars = sum(prompt_chars) / max(len(prompt_chars), 1)
    avg_completion_chars = sum(completion_chars) / max(len(completion_chars), 1)
    avg_prompt_tokens = sum(prompt_tokens) / max(len(prompt_tokens), 1)
    avg_completion_tokens = sum(completion_tokens) / max(len(completion_tokens), 1)

    # Language distribution
    lang_counts: Counter = Counter()
    for p in sample:
        lang = _detect_lang_simple(p.get("prompt", "") + " " + p.get("completion", ""))
        lang_counts[lang] += 1

    # Build HTML
    sections = []

    # Header
    sections.append(f'''
    <header>
      <h1>{_escape(title)}</h1>
      <p class="meta">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
    </header>''')

    # Summary cards
    cards = []
    cards.append(f'<div class="card"><div class="card-value">{total}</div><div class="card-label">Total pairs</div></div>')
    cards.append(f'<div class="card"><div class="card-value">{len(by_type)}</div><div class="card-label">Task types</div></div>')
    cards.append(f'<div class="card"><div class="card-value">{avg_prompt_chars:.0f}</div><div class="card-label">Avg prompt chars</div></div>')
    cards.append(f'<div class="card"><div class="card-value">{avg_completion_chars:.0f}</div><div class="card-label">Avg completion chars</div></div>')
    cards.append(f'<div class="card"><div class="card-value">{avg_prompt_tokens:.0f}</div><div class="card-label">Avg prompt tokens</div></div>')
    cards.append(f'<div class="card"><div class="card-value">{avg_completion_tokens:.0f}</div><div class="card-label">Avg completion tokens</div></div>')
    cards_html = "".join(cards)
    sections.append(f'<section><h2>Summary</h2><div class="cards">{cards_html}</div></section>')

    # Bar chart: pairs by type
    type_data = sorted(by_type.items(), key=lambda x: -x[1])
    sections.append(f'<section><h2>Pairs by task type</h2>{_bar_chart_svg(type_data, "Pair count by task_type")}</section>')

    # Histograms
    sections.append(f'<section><h2>Length distributions (sample of {len(sample)} pairs)</h2>'
                    f'<div class="charts-grid">'
                    f'<div>{_histogram_svg(prompt_chars, "Prompt length (chars)")}</div>'
                    f'<div>{_histogram_svg(completion_chars, "Completion length (chars)")}</div>'
                    f'<div>{_histogram_svg(prompt_tokens, "Prompt length (tokens)")}</div>'
                    f'<div>{_histogram_svg(completion_tokens, "Completion length (tokens)")}</div>'
                    f'</div></section>')

    # Language distribution
    if lang_counts:
        lang_data = sorted(lang_counts.items(), key=lambda x: -x[1])
        sections.append('<section><h2>Language distribution</h2>'
                        '<table class="data-table"><thead><tr><th>Language</th><th>Count</th><th>%</th></tr></thead><tbody>')
        for lang, cnt in lang_data:
            pct = cnt * 100 / max(len(sample), 1)
            sections.append(f'<tr><td>{_escape(lang)}</td><td>{cnt}</td><td>{pct:.1f}%</td></tr>')
        sections.append('</tbody></table></section>')

    # Detailed type breakdown table
    sections.append('<section><h2>Detailed breakdown by type</h2>'
                    '<table class="data-table"><thead><tr><th>Task type</th><th>Count</th><th>% of total</th><th>Avg prompt chars</th><th>Avg completion chars</th></tr></thead><tbody>')
    type_avg: dict[str, dict[str, float]] = {}
    for p in sample:
        t = p.get("task_type", "unknown")
        if t not in type_avg:
            type_avg[t] = {"prompt_sum": 0, "completion_sum": 0, "count": 0}
        type_avg[t]["prompt_sum"] += len(p.get("prompt", ""))
        type_avg[t]["completion_sum"] += len(p.get("completion", ""))
        type_avg[t]["count"] += 1
    for t, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
        pct = cnt * 100 / max(total, 1)
        avg_p = type_avg.get(t, {}).get("prompt_sum", 0) / max(type_avg.get(t, {}).get("count", 1), 1)
        avg_c = type_avg.get(t, {}).get("completion_sum", 0) / max(type_avg.get(t, {}).get("count", 1), 1)
        sections.append(f'<tr><td>{_escape(t)}</td><td>{cnt}</td><td>{pct:.1f}%</td>'
                        f'<td>{avg_p:.0f}</td><td>{avg_c:.0f}</td></tr>')
    sections.append('</tbody></table></section>')

    # Pipeline stages (if stats provided)
    if stats and stats.get("stage_counts"):
        stage_counts = stats["stage_counts"]
        sections.append('<section><h2>Pipeline stages</h2>'
                        '<table class="data-table"><thead><tr><th>Stage</th><th>Total</th></tr></thead><tbody>')
        for stage, sc in stage_counts.items():
            sections.append(f'<tr><td>{_escape(stage)}</td><td>{sum(sc.values())}</td></tr>')
        sections.append('</tbody></table></section>')

    # Warnings
    if stats and stats.get("warnings"):
        sections.append(f'<section><h2>⚠ Warnings ({len(stats["warnings"])})</h2><ul class="warnings">')
        for w in stats["warnings"]:
            sections.append(f'<li class="warning">{_escape(w)}</li>')
        sections.append('</ul></section>')

    # Compose full HTML
    full_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_escape(title)}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    max-width: 1200px;
    margin: 0 auto;
    padding: 20px;
    background: #f5f5f5;
    color: #333;
    line-height: 1.6;
  }}
  header {{
    background: #007acc;
    color: white;
    padding: 20px 30px;
    border-radius: 8px;
    margin-bottom: 20px;
  }}
  header h1 {{ margin: 0; font-size: 24px; }}
  header .meta {{ margin: 5px 0 0; opacity: 0.85; font-size: 13px; }}
  section {{
    background: white;
    padding: 20px;
    border-radius: 8px;
    margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }}
  h2 {{
    margin-top: 0;
    color: #007acc;
    border-bottom: 2px solid #e0e0e0;
    padding-bottom: 8px;
    font-size: 18px;
  }}
  .cards {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 15px;
  }}
  .card {{
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 20px;
    border-radius: 8px;
    text-align: center;
  }}
  .card-value {{
    font-size: 32px;
    font-weight: bold;
    line-height: 1;
  }}
  .card-label {{
    font-size: 12px;
    margin-top: 5px;
    opacity: 0.9;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .charts-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(600px, 1fr));
    gap: 20px;
  }}
  .data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
  }}
  .data-table th, .data-table td {{
    padding: 8px 12px;
    text-align: left;
    border-bottom: 1px solid #eee;
  }}
  .data-table th {{
    background: #f8f9fa;
    font-weight: 600;
    color: #555;
  }}
  .data-table tr:hover {{ background: #f8f9fa; }}
  .warnings {{ list-style: none; padding: 0; }}
  .warning {{
    background: #fff3cd;
    border-left: 4px solid #ffc107;
    padding: 10px 15px;
    margin-bottom: 8px;
    border-radius: 4px;
    font-size: 14px;
  }}
  svg {{ max-width: 100%; height: auto; }}
</style>
</head>
<body>
{''.join(sections)}
</body>
</html>'''

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(full_html)

    log.info(f"HTML report written to {output_file} ({total} pairs)")
    return str(output_file)
