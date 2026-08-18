"""Удаление персональных данных (PII) из текста."""
from __future__ import annotations
import re

EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
PHONE_RE = re.compile(r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b')
IP_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
SSN_RE = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')

def remove_pii(text: str, replace_with: str = "[REDACTED]") -> str:
    """Удалить PII из текста."""
    text = EMAIL_RE.sub(replace_with, text)
    text = PHONE_RE.sub(replace_with, text)
    text = IP_RE.sub(replace_with, text)
    text = SSN_RE.sub(replace_with, text)
    return text

def clean_pair(pair: dict) -> dict:
    """Очистить пару от PII."""
    pair["prompt"] = remove_pii(pair.get("prompt", ""))
    pair["completion"] = remove_pii(pair.get("completion", ""))
    return pair
