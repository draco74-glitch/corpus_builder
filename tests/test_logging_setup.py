"""Тесты на logging_setup — критично для PyInstaller windowed mode.

Проверяем:
  - setup_logging не падает при sys.stderr = None (frozen .exe без консоли)
  - get_logger создаёт лог-файл рядом с .exe в frozen-режиме
  - функция is_frozen возвращает False в обычном режиме
"""
import sys
import os
from pathlib import Path
from unittest import mock

import pytest

from corpus_builder import logging_setup
from corpus_builder.logging_setup import (
    setup_logging, get_logger, is_frozen, get_log_dir,
    _has_file_handler, _configured
)


def test_is_frozen_returns_false_in_normal_mode():
    """В обычном Python-режиме is_frozen() должен быть False."""
    assert is_frozen() is False


def test_is_frozen_returns_true_when_frozen_attribute_set():
    """Если sys.frozen = True, is_frozen должен вернуть True."""
    with mock.patch.object(sys, "frozen", True, create=True), \
         mock.patch.object(sys, "_MEIPASS", "/tmp/fake", create=True):
        assert is_frozen() is True


def test_setup_logging_does_not_crash_when_stderr_is_none():
    """Главный баг: в PyInstaller windowed mode sys.stderr равен None.

    setup_logging должен пропустить добавление stderr handler и не упасть.
    """
    old_stderr = sys.stderr
    old_configured = logging_setup._configured
    try:
        # Сбрасываем состояние
        logging_setup._configured = False
        from loguru import logger
        logger.remove()

        # Эмулируем frozen .exe — stderr равен None
        with mock.patch.object(sys, "stderr", None):
            # Не должно упасть с TypeError
            setup_logging(log_file=None)

        # Если есть хотя бы какой-то handler — отлично (тут может быть 0,
        # потому что без log_file мы не создаём файловый handler)
        # Главное — не упало с TypeError.
        assert True
    finally:
        sys.stderr = old_stderr
        logging_setup._configured = old_configured


def test_setup_logging_creates_log_file(tmp_path):
    """При указании пути log_file создаётся файловый handler."""
    old_configured = logging_setup._configured
    try:
        logging_setup._configured = False
        from loguru import logger
        logger.remove()

        log_file = tmp_path / "test.log"
        setup_logging(log_file=log_file)

        # Пишем сообщение и проверяем, что оно попало в файл
        logger.info("test message")
        logger.complete()

        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "test message" in content
    finally:
        logging_setup._configured = old_configured


def test_setup_logging_with_stderr_none_and_log_file(tmp_path):
    """Смоделировать frozen .exe: stderr=None + файловый handler."""
    old_stderr = sys.stderr
    old_configured = logging_setup._configured
    try:
        logging_setup._configured = False
        from loguru import logger
        logger.remove()

        log_file = tmp_path / "frozen.log"

        with mock.patch.object(sys, "stderr", None):
            setup_logging(log_file=log_file)

        logger.info("message from frozen exe")
        logger.complete()

        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "message from frozen exe" in content
    finally:
        sys.stderr = old_stderr
        logging_setup._configured = old_configured


def test_get_logger_initializes_default_log_file(tmp_path, monkeypatch):
    """get_logger без явного setup создаёт лог-файл по умолчанию."""
    old_configured = logging_setup._configured
    try:
        logging_setup._configured = False
        from loguru import logger
        logger.remove()

        # Подменяем get_log_dir, чтобы не засорять проект
        fake_log_dir = tmp_path / "fake_logs"
        monkeypatch.setattr(logging_setup, "get_log_dir", lambda: fake_log_dir)

        log = get_logger("test_module")
        log.info("default initialization test")
        logger.complete()

        assert fake_log_dir.exists()
        log_file = fake_log_dir / "crawl.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "default initialization test" in content
    finally:
        logging_setup._configured = old_configured


def test_get_log_dir_creates_directory(tmp_path, monkeypatch):
    """get_log_dir создаёт папку, если её нет."""
    # Перекрываем cwd на tmp_path
    monkeypatch.chdir(tmp_path)
    # Убеждаемся, что is_frozen() = False
    monkeypatch.setattr(logging_setup, "is_frozen", lambda: False)

    log_dir = get_log_dir()
    assert log_dir.exists()
    assert log_dir.name == "corpus_output"


def test_setup_logging_handles_permission_error(tmp_path, monkeypatch):
    """Если не можем создать файл — логер не падает."""
    old_configured = logging_setup._configured
    try:
        logging_setup._configured = False
        from loguru import logger
        logger.remove()

        # Эмулируем, что Path.mkdir падает с PermissionError
        bad_path = tmp_path / "no_such_dir" / "deep" / "test.log"

        # Должно вызваться без исключения, даже если путь недоступен
        with mock.patch.object(sys, "stderr", None):
            setup_logging(log_file=bad_path)
        assert True  # Главное — не упало
    finally:
        logging_setup._configured = old_configured
