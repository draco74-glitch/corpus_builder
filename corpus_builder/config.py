"""Загрузка и валидация конфигурации."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .logging_setup import get_logger
from .models import AppConfig

log = get_logger(__name__)


def load_config(path: str | Path) -> AppConfig:
    """Загрузить YAML-конфиг и провалидировать через pydantic."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    if not raw:
        raise ValueError(f"Config file is empty: {path}")
    try:
        return AppConfig(**raw)
    except ValidationError as e:
        log.error("Config validation failed:")
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            log.error(f"  - {loc}: {err['msg']}")
        raise


def ensure_output_dirs(config: AppConfig) -> None:
    """Создать выходные директории."""
    Path(config.output.corpus_file).parent.mkdir(parents=True, exist_ok=True)
    Path(config.output.download_dir).mkdir(parents=True, exist_ok=True)
    Path(config.output.state_file).parent.mkdir(parents=True, exist_ok=True)
    Path(config.output.error_log).parent.mkdir(parents=True, exist_ok=True)
    Path(config.output.log_file).parent.mkdir(parents=True, exist_ok=True)
