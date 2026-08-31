"""CLI для corpus-builder."""
from __future__ import annotations

from pathlib import Path

import click

from .logging_setup import get_logger, setup_logging

log = get_logger(__name__)


@click.group()
@click.option("--config", "-c", default="config.yaml",
              help="Путь к YAML-конфигу (по умолчанию config.yaml)")
@click.option("--verbose", "-v", is_flag=True, help="DEBUG-логирование")
@click.pass_context
def cli(ctx, config: str, verbose: bool):
    """corpus-builder: сбор сырого корпуса для pretraining LLM."""
    from .config import load_config
    setup_logging(Path("corpus_output") / "crawl.log", verbose=verbose)
    cfg = load_config(config)
    ctx.obj = cfg


@cli.command()
@click.option("--resume/--no-resume", default=None, help="Продолжить с последнего чекпойнта")
@click.option("--limit", type=int, default=None, help="Обработать только первые N источников")
@click.option("--source-type", type=str, default=None,
              help="Только источники указанного типа (html, pdf, github_repo, stackexchange, "
                   "forum, doaj, arxiv, crossref, wikipedia)")
@click.option("--dry-run", is_flag=True, help="Только показать, что будет обработано")
@click.option("--async/--sync", "use_async", default=None,
              help="Явно выбрать асинхронный/синхронный краулинг "
                   "(по умолчанию — pipeline.use_async из config.yaml)")
@click.pass_obj
def crawl(cfg, resume, limit, source_type, dry_run, use_async):
    """Запустить краулинг (синхронный или асинхронный)."""
    import asyncio

    from .async_pipeline import run_async_crawl
    from .pipeline import run_crawl

    resume = cfg.pipeline.resume if resume is None else resume
    if use_async is None:
        use_async = cfg.pipeline.use_async

    if dry_run and not resume:
        # dry-run ничего не пишет — усекать корпуса не нужно
        pass
    if use_async:
        stats = asyncio.run(run_async_crawl(
            cfg, resume=resume, limit=limit, source_type=source_type,
            max_concurrent_total=cfg.pipeline.max_concurrent_total,
            max_concurrent_per_domain=cfg.pipeline.max_concurrent_per_domain,
        ))
    else:
        stats = run_crawl(cfg, resume=resume, limit=limit, source_type=source_type,
                          dry_run=dry_run)
    click.echo(json_dump(stats))


@cli.command(name="async-crawl")
@click.option("--resume/--no-resume", default=None, help="Продолжить с последнего чекпойнта")
@click.option("--limit", type=int, default=None, help="Обработать только первые N источников")
@click.option("--source-type", type=str, default=None,
              help="Только источники указанного tipo")
@click.option("--max-concurrent", type=int, default=8,
              help="Максимум одновременных запросов (по умолчанию 8)")
@click.option("--max-concurrent-per-domain", type=int, default=1,
              help="Максимум одновременных запросов на один домен (1 = вежливо)")
@click.pass_obj
def async_crawl(cfg, resume, limit, source_type, max_concurrent, max_concurrent_per_domain):
    """Запустить асинхронный краулинг (ускорение 4-8x для смешанных доменов)."""
    import asyncio

    from .async_pipeline import run_async_crawl
    resume = cfg.pipeline.resume if resume is None else resume
    stats = asyncio.run(run_async_crawl(
        cfg, resume=resume, limit=limit, source_type=source_type,
        max_concurrent_total=max_concurrent,
        max_concurrent_per_domain=max_concurrent_per_domain,
    ))
    click.echo(json_dump(stats))


@cli.command(name="postprocess")
@click.pass_obj
def postprocess(cfg):
    """Пост-обработка: дедупликация + фильтр + нормализация + пары."""
    from .pipeline import run_postprocess
    stats = run_postprocess(cfg)
    click.echo(json_dump(stats))


@cli.command(name="estimate")
@click.option("--source-type", default=None, help="Только источники указанного типа")
@click.pass_obj
def estimate(cfg, source_type):
    """Оценка времени краулинга по вежливым задержкам (без запросов)."""
    from .pipeline import estimate_crawl_minutes
    sources = [x for x in cfg.sources if not source_type or x.type == source_type]
    domains = {x.url.split("/")[2] if "//" in x.url else x.url for x in sources}
    minutes = estimate_crawl_minutes(sources, cfg.output.request_delay)
    click.echo(json_dump({
        "sources": len(sources),
        "domains": len(domains),
        "request_delay_s": cfg.output.request_delay,
        "min_wait_minutes": round(minutes, 1),
        "note": ("задержки действуют на домен и не считаются при попадании в "
                 "HTTP-кэш; реальное время выше из-за сети и разбора"),
    }))


@cli.command(name="stats")
@click.pass_obj
def stats_cmd(cfg):
    """Показать статистику по собранному корпусу."""
    from .state import State
    state = State(cfg.output.state_file)
    corpus_path = Path(cfg.output.corpus_file)
    if corpus_path.exists():
        with open(corpus_path, "r", encoding="utf-8") as f:
            lines = sum(1 for _ in f)
    else:
        lines = 0
    out = {
        "done_urls": state.done_count,
        "error_urls": state.error_count,
        "corpus_records": lines,
        "corpus_file": str(corpus_path),
        "state_file": str(cfg.output.state_file),
    }
    click.echo(json_dump(out))


@cli.command()
@click.argument("old_file", type=click.Path(exists=True))
@click.argument("new_file", type=click.Path(exists=True))
@click.option("--html", "html_output", default=None,
              help="Сгенерировать HTML-отчёт по указанному пути")
def diff(old_file, new_file, html_output):
    """Сравнить два корпуса (JSONL) — что нового появилось.

    \b
    Пример:
      corpus-builder diff corpus_old.jsonl corpus_new.jsonl --html report.html
    """
    from .diff import diff_corpora
    result = diff_corpora(old_file, new_file, html_output=html_output)
    click.echo(json_dump({
        "stats": result["stats"],
        "added_top5": [
            {"url": r.get("source_url", ""), "type": r.get("source_type", "")}
            for r in result["added"][:5]
        ],
        "removed_top5": [
            {"url": r.get("source_url", ""), "type": r.get("source_type", "")}
            for r in result["removed"][:5]
        ],
    }))
    if html_output:
        click.echo(f"\nHTML-отчёт сохранён: {html_output}")


@cli.command()
@click.option("--build-dir", default="dist/CorpusBuilder",
              help="Собранные one-dir артефакты PyInstaller")
@click.option("--output", "output_zip", default=None, help="Куда положить ZIP")
@click.option("--version", default=None, help="Версия в имени файла (по умолчанию из пакета)")
@click.option("--patch-only", is_flag=True,
              help="Собрать только patch.zip (.py файлы для авто-обновления)")
@click.pass_obj
def package(cfg, build_dir, output_zip, version, patch_only):
    """Собрать ZIP-дистрибутив (или patch.zip) из готовой сборки."""
    from . import __version__
    from .zip_distributor import create_distribution, create_patch_only

    ver = version or __version__
    if patch_only:
        out = create_patch_only("corpus_builder",
                                output_zip or f"dist/patch-{ver}.zip",
                                version=ver)
        click.echo(json_dump({"patch": out}))
        return
    info = create_distribution(build_dir, output_zip=output_zip, version=ver)
    click.echo(json_dump(info))


@cli.command(name="export")
@click.option("--format", "fmt", type=click.Choice(["hf", "parquet", "both"]),
              default="both", help="Формат экспорта финального корпуса")
@click.option("--out", "out_dir", default=None,
              help="Куда экспортировать (по умолчанию — рядом с corpus_file)")
@click.pass_obj
def export_cmd(cfg, fmt, out_dir):
    """Экспортировать corpus_final.jsonl в HuggingFace/Parquet из CLI."""
    from pathlib import Path

    from .postproc.export import export_huggingface, export_parquet

    final = Path(cfg.output.corpus_file).parent / "corpus_final.jsonl"
    if not final.exists():
        raise click.ClickException(
            f"{final} не найден — сначала выполните postprocess")
    base = Path(out_dir) if out_dir else final.parent
    result = {}
    if fmt in ("hf", "both"):
        result["huggingface"] = export_huggingface(final, base / "corpus_hf_dataset")
    if fmt in ("parquet", "both"):
        result["parquet"] = export_parquet(final, base / "corpus.parquet")
    click.echo(json_dump(result))


def json_dump(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2)


def main():
    """Точка входа для setuptools console_scripts."""
    cli(obj=None)


if __name__ == "__main__":
    main()
