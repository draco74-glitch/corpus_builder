"""Утилита для создания ZIP-дистрибутива CorpusBuilder.

Используется после сборки PyInstaller для упаковки папки dist/CorpusBuilder/
в ZIP-архив, готовый к распространению.

Дополнительно создаёт patch.zip — только .py файлы для авто-обновления.

Использование:
    python -m corpus_builder.zip_distributor

Или программно:
    from corpus_builder.zip_distributor import create_distribution
    create_distribution("dist/CorpusBuilder", "dist/CorpusBuilder-0.2.0.zip")

Или из CLI собранного проекта:
    corpus-builder package --build-dir dist/CorpusBuilder --zip

NOTE: это инструмент сборки, не часть рантайма: он вызывается из build.sh,
`python -m corpus_builder.zip_distributor` и CLI-команды `package`.
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

from .logging_setup import get_logger

log = get_logger(__name__)


def create_distribution(
    build_dir: str | Path = "dist/CorpusBuilder",
    output_zip: str | Path | None = None,
    version: str = "0.2.0",
    include_patch: bool = True,
) -> dict:
    """Создать ZIP-дистрибутив из собранной папки.

    Параметры:
        build_dir: папка dist/CorpusBuilder/ (результат PyInstaller one-dir)
        output_zip: путь к выходному ZIP (если None — auto-generate)
        version: версия для имени файла
        include_patch: создать также patch.zip для авто-обновления

    Возвращает dict с путями к созданным файлам.
    """
    build_dir = Path(build_dir)
    if not build_dir.exists():
        raise FileNotFoundError(f"Build directory not found: {build_dir}")

    if output_zip is None:
        output_zip = build_dir.parent / f"CorpusBuilder-{version}.zip"
    output_zip = Path(output_zip)

    # Создаём основной ZIP
    log.info(f"Creating distribution ZIP: {output_zip}")
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(build_dir):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(build_dir.parent)
                zf.write(file_path, arcname)

    zip_size = output_zip.stat().st_size
    zip_size_mb = zip_size / (1024 * 1024)
    log.info(f"Distribution ZIP: {output_zip} ({zip_size_mb:.1f} MB)")

    result = {
        "distribution_zip": str(output_zip),
        "distribution_size": zip_size,
        "distribution_size_mb": round(zip_size_mb, 1),
    }

    # Создаём patch.zip (только .py файлы для авто-обновления)
    if include_patch:
        patch_zip = output_zip.parent / f"patch-{version}.zip"
        source_dir = build_dir / "_internal" / "corpus_builder"
        if not source_dir.exists():
            source_dir = build_dir / "corpus_builder"

        if source_dir.exists():
            log.info(f"Creating patch ZIP: {patch_zip}")
            with zipfile.ZipFile(patch_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for py_file in source_dir.rglob("*.py"):
                    rel_path = py_file.relative_to(source_dir)
                    zf.write(py_file, f"corpus_builder/{rel_path}")

            patch_size = patch_zip.stat().st_size
            patch_size_kb = patch_size / 1024
            log.info(f"Patch ZIP: {patch_zip} ({patch_size_kb:.1f} KB)")
            result["patch_zip"] = str(patch_zip)
            result["patch_size"] = patch_size
            result["patch_size_kb"] = round(patch_size_kb, 1)
        else:
            log.warning(f"Source directory not found: {source_dir}")

    return result


def create_patch_only(
    source_dir: str | Path = "corpus_builder",
    output_zip: str | Path | None = None,
    version: str = "0.2.0",
    changed_files: list[str] | None = None,
) -> str:
    """Создать только patch.zip (для обновления без полного дистрибутива).

    Параметры:
        source_dir: папка с .py файлами (corpus_builder/)
        output_zip: путь к выходному ZIP
        version: версия
        changed_files: только эти файлы (если None — все .py)
    """
    from .auto_updater import AutoUpdater

    if output_zip is None:
        output_zip = f"patch-{version}.zip"

    return AutoUpdater.create_patch_zip(source_dir, output_zip, changed_files)


def print_distribution_info(dist_info: dict) -> None:
    """Вывести информацию о созданном дистрибутиве."""
    print("\n" + "=" * 60)
    print("  Distribution created successfully")
    print("=" * 60)
    print("\n  Full distribution:")
    print(f"    File: {dist_info['distribution_zip']}")
    print(f"    Size: {dist_info['distribution_size_mb']} MB")

    if "patch_zip" in dist_info:
        print("\n  Auto-update patch:")
        print(f"    File: {dist_info['patch_zip']}")
        print(f"    Size: {dist_info['patch_size_kb']} KB")
        ratio = dist_info['distribution_size'] / max(dist_info.get('patch_size', 1), 1)
        print(f"    Ratio: {ratio:.0f}x smaller than full distribution")

    print("\n  Upload to GitHub Releases:")
    print(f"    1. Full: {Path(dist_info['distribution_zip']).name}")
    if "patch_zip" in dist_info:
        print(f"    2. Patch: {Path(dist_info['patch_zip']).name}")
    print("\n  Users will auto-update by downloading patch.zip")
    print("=" * 60 + "\n")


def main():
    """CLI точка входа для создания дистрибутива."""
    import argparse

    parser = argparse.ArgumentParser(description="Create CorpusBuilder distribution ZIP")
    parser.add_argument("--build-dir", default="dist/CorpusBuilder",
                        help="Path to PyInstaller build directory")
    parser.add_argument("--output", default=None,
                        help="Output ZIP path (auto-generated if not specified)")
    parser.add_argument("--version", default="0.2.0",
                        help="Version string for filename")
    parser.add_argument("--no-patch", action="store_true",
                        help="Skip creating patch.zip")

    args = parser.parse_args()

    dist_info = create_distribution(
        build_dir=args.build_dir,
        output_zip=args.output,
        version=args.version,
        include_patch=not args.no_patch,
    )
    print_distribution_info(dist_info)


if __name__ == "__main__":
    main()
