"""CLI для corpus-builder."""
from __future__ import annotations

import re
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
    ctx.obj = None
    try:
        ctx.obj = load_config(config)
    except Exception as e:
        # Б9: битый конфиг раньше давал traceback на ЛЮБОЙ команде, включая
        # `validate` — то есть проверить конфиг было нельзя.
        # многострочный дамп pydantic в консоли нечитаем — оставляем первую строку
        detail = str(e).strip().splitlines() or ["не известна"]
        ctx.meta["config_error"] = f"{type(e).__name__}: {detail[0]}"
        if (ctx.invoked_subcommand or "") not in ("validate", "schema", "preset"):
            click.echo(f"Ошибка конфигурации {config}: {ctx.meta['config_error']}",
                       err=True)
            raise SystemExit(2)


def _need_config(ctx: click.Context):
    cfg = getattr(ctx, "obj", None)
    if cfg is None:
        raise SystemExit(f"Ошибка конфигурации: "
                         f"{ctx.meta.get('config_error', 'не загружена')}")
    return cfg


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


@cli.command(name="preset")
@click.argument("key", required=False, default=None)
@click.option("--apply", "apply_", is_flag=True,
              help="Применить пресет к настройкам приложения (как в диалоге)")
@click.option("--yaml", "yaml_out", default=None, metavar="FILE",
              help="Записать поля пресета как YAML-накидку к config.yaml")
def preset_cmd(key, apply_, yaml_out):
    """Готовые профили: polite / own_site / academic / big_corpus (+ свои).

    Без KEY — список. `preset academic` — показать поля. `--apply` — применить к
    настройкам GUI, `--yaml ФАЙЛ` —.dump накидку, которую можно слить с
    config.yaml (команда `merge-configs`, если она у вас есть).
    """
    from .presets import all_presets, apply_preset, preset_by_key, validate_preset

    if not key:
        click.echo(json_dump([{"key": p.key, "title": p.title, "builtin": p.builtin,
                               "description": p.description, "fields": len(p.values)}
                              for p in all_presets()]))
        click.echo("показать: corpus-builder preset KEY | применить: --apply")
        return

    preset = preset_by_key(key)
    if preset is None:
        known = ", ".join(p.key for p in all_presets())
        raise SystemExit(f"неизвестный пресет: {key} (известны: {known})")
    problems = validate_preset(preset)
    if problems:
        raise SystemExit("пресет не применим: " + "; ".join(problems))

    if apply_:
        from .app_settings import AppSettings
        settings = AppSettings.load()
        changed = apply_preset(settings, preset.key)
        settings.save()
        click.echo(f"Пресет «{preset.title}» применён: изменено полей — {len(changed)}")
        click.echo(json_dump(sorted(changed)))
        return

    if yaml_out:
        import yaml as _yaml

        from .app_settings import AppSettings
        targets = dict(AppSettings().mapping())
        tree: dict = {}
        skipped = []
        for path, value in preset.values.items():
            if path not in targets or path.startswith("gui."):
                skipped.append(path)
                continue
            parts = targets[path].split(".")
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        header = (f"# Накладка пресета «{preset.title}»: {preset.description}\n"
                  f"# Полей без места в config.yaml пропущено: "
                  f"{', '.join(skipped) if skipped else 'нет'}\n")
        Path(yaml_out).write_text(
            header + _yaml.safe_dump(tree, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        click.echo(f"Накидка пресета «{preset.title}» записана: {yaml_out}")
        return

    click.echo(json_dump({"key": preset.key, "title": preset.title,
                          "description": preset.description,
                          "values": preset.values}))


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


_EMAIL_IN_UA = re.compile(r"mailto:\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def _canon(url: str) -> str:
    """Канон URL для проверки дублей — так же, как его канонит краул."""
    from .text_utils import canonical_url
    try:
        return canonical_url(url)
    except Exception:
        return url.strip().lower()


def config_warnings(cfg) -> list[str]:
    """Мягкие замечания: конфиг валиден, но краулинг пройдёт не так, как ждём."""
    out: list[str] = []
    if not cfg.sources:
        out.append("⚠ список sources пуст — крауливать нечего")
    ua = (cfg.output.user_agent or "")
    if not (_EMAIL_IN_UA.search(ua) or (cfg.output.contact_email or "").strip()):
        polite = {"arxiv", "crossref", "doaj", "wikipedia", "stackexchange"}
        hit = sorted({s.type for s in cfg.sources} & polite)
        if hit:
            out.append(
                "⚠ нет контактного e-mail (output.contact_email или mailto: в "
                f"user_agent): типы {hit} требуют «polite»-идентификацию, иначе "
                "уходят 403 Too Many Requests")
    seen: dict[str, list[int]] = {}
    for i, s in enumerate(cfg.sources, 1):
        if not (s.url or "").strip():
            out.append(f"✗ sources[{i}]: пустой url")
            continue
        seen.setdefault(_canon(s.url), []).append(i)
    for canon, idxs in seen.items():
        if len(idxs) > 1:
            out.append(f"⚠ источники {idxs} — один URL после нормализации "
                       f"({canon[:70]}): второй будет пропущен как уже сделанный")
    if cfg.output.request_delay < 0.05:
        out.append(f"⚠ output.request_delay={cfg.output.request_delay}: "
                   "это спам-режим, сайты банят по IP")
    if cfg.quality.min_chars <= 0:
        out.append("⚠ quality.min_chars<=0: фильтр по длине выключен, в корпус "
                   "попадут заглушки и страницы-ошибки")
    if not cfg.output.respect_robots_txt:
        out.append("⚠ output.respect_robots_txt=false: игнорируем robots.txt — "
                   "так не надо делать без явной причины")
    if cfg.pipeline.per_url_timeout_minutes <= 0:
        out.append("⚠ pipeline.per_url_timeout_minutes<=0: зависший источник "
                   "остановит весь прогон")
    return out


def validate_config_file(path: str | Path) -> list[str]:
    """Проверить YAML-конфиг, ничего не запуская.

    Возвращает список проблем (пусто = всё хорошо). Исключений не бросает —
    функцию вызывают из GUI (Ctrl+Shift+V).
    """
    import yaml
    from pydantic import ValidationError
    from .models import AppConfig

    p = Path(path)
    if not p.exists():
        return [f"✗ файл не найден: {p}"]
    try:
        raw_text = p.read_text(encoding="utf-8")
    except OSError as e:
        return [f"✗ файл не читается: {e}"]
    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (строка {mark.line + 1}, колонка {mark.column + 1})" if mark else ""
        return [f"✗ синтаксис YAML{where}: {getattr(e, 'problem', None) or e}"]
    if raw is None:
        return [f"✗ файл пустой: {p}"]
    if not isinstance(raw, dict):
        return ["✗ корень YAML должен быть словарём вида sources: / output: / ..."]
    try:
        cfg = AppConfig(**raw)
    except ValidationError as e:
        problems = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"]) or "<корень>"
            problems.append(f"✗ {loc}: {err['msg']}")
        return problems or ["✗ конфиг не прошёл валидацию"]
    except Exception as e:
        return [f"✗ конфиг не разобран: {type(e).__name__}: {e}"]
    return config_warnings(cfg)


@cli.command(name="validate")
@click.option("--config", "-c", "config_path", default=None,
              help="Проверить указанный файл вместо -c (можно битый конфиг)")
@click.option("--strict", is_flag=True, help="Считать замечания (⚠) ошибкой")
@click.pass_context
def validate_cmd(ctx, config_path, strict):
    """Проверить корректность YAML-конфига без запуска краулинга."""
    path = (config_path or (ctx.parent.params.get("config") if ctx.parent else None)
            or "config.yaml")
    problems = validate_config_file(path)
    errors = [x for x in problems if x.startswith("✗")]
    warns = [x for x in problems if x.startswith("⚠")]
    for line in problems:
        click.echo(line)
    if errors or (strict and warns):
        click.echo(f"Невалидно: {path} "
                   f"(ошибок {len(errors)}, замечаний {len(warns)})")
        raise SystemExit(1)
    if warns:
        click.echo(f"Валидно, но есть замечания ({len(warns)}): {path}")
    else:
        click.echo(f"Валидно: {path}")


@cli.command(name="schema")
@click.option("--out", "out_path", default=None, help="Записать JSON-схему в файл")
def schema_cmd(out_path):
    """JSON-схема конфига (для проверки редакторами / внешними валидаторами).

    Пример: `corpus-builder schema --out corpus.schema.json` — и redhat.vscode-yaml
    начнёт подсказывать поля; ссылка на схему стоит в шапке config.example.yaml.
    """
    import json
    from .models import AppConfig
    schema = json.dumps(AppConfig.model_json_schema(), ensure_ascii=False, indent=2)
    if out_path:
        Path(out_path).write_text(schema, encoding="utf-8")
        click.echo(f"Схема записана: {out_path}")
    else:
        click.echo(schema)


def json_dump(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2)


def main():
    """Точка входа для setuptools console_scripts."""
    cli(obj=None)


if __name__ == "__main__":
    main()
