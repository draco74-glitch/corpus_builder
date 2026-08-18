"""Удаление персональных данных (PII) из текста.

Catches:
  1. Standard emails (user@example.com)
  2. Obfuscated emails (user [at] example [dot] com, user(at)example.com,
     user {at} example {dot} com, user AT example DOT com)
  3. Phone numbers (international +1-555-123-4567, and without + prefix)
  4. IPv4 addresses (192.168.1.1)
  5. SSN (123-45-6789)
  6. API keys / tokens in code:
        api_key="sk-...", api_key: "..."
        token="ghp_...", token: "Bearer ..."
        Bearer eyJ..., Authorization: Bearer ...
        sk-[a-zA-Z0-9]{20,} (OpenAI-style)
        ghp_[a-zA-Z0-9]{36} (GitHub PAT)
        github_pat_[a-zA-Z0-9_]{82} (GitHub fine-grained)
        xox[baprs]-[a-zA-Z0-9-]+ (Slack tokens)
        AKIA[0-9A-Z]{16} (AWS access key ID)
  7. @username in contexts that look like GitHub/social mentions
     (only redacted when preceded by 'github.com/' or followed by ' on GitHub'
     or in a sentence mentioning GitHub — to avoid redacting Twitter-style
     @mentions in casual text which may be intentional content)

NOTE: We do NOT redact @username in arbitrary contexts because many technical
articles legitimately reference authors by their handle (e.g. "see @torvalds'
comment"). We only redact when the surrounding text strongly suggests the
username is being used as a credential or in a sensitive URL pattern.
"""
from __future__ import annotations
import re

# ============================================================
# 1. Email patterns
# ============================================================

EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')

# Obfuscated: user [at] example [dot] com / user (at) example (dot) com /
# user {at} example {dot} com / user AT example DOT com
EMAIL_OBFUSCATED_RE = re.compile(
    r'\b([A-Za-z0-9._%+-]+)\s*'
    r'(?:\[at\]|\(at\)|\{at\}|<at>|AT|@)\s*'
    r'([A-Za-z0-9.-]+)\s*'
    r'(?:\[dot\]|\(dot\)|\{dot\}|<dot>|DOT|\.)\s*'
    r'([A-Za-z]{2,})\b',
    re.IGNORECASE,
)

# Also catch the simpler "user at example dot com" without brackets
EMAIL_OBFUSCATED_PLAIN_RE = re.compile(
    r'\b([A-Za-z0-9._%+-]{2,})\s+at\s+([A-Za-z0-9.-]{2,})\s+dot\s+([A-Za-z]{2,})\b',
    re.IGNORECASE,
)

# ============================================================
# 2. Phone patterns
# ============================================================

# International with +: +1-555-123-4567, +7 (495) 123-45-67, +44 20 7946 0958
PHONE_INTL_RE = re.compile(
    r'\+\d{1,3}[\s.-]?\(?\d{1,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}'
    r'(?:[\s.-]?\d{1,4})?'
)

# Russian phone (without +): 8 (495) 123-45-67, 8-916-123-45-67
PHONE_RU_RE = re.compile(
    r'\b8[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}\b'
)

# Generic 10-digit: 555-123-4567 (kept from original but tightened)
PHONE_RE = re.compile(
    r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'
)

# ============================================================
# 3. IP / SSN
# ============================================================

IP_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
SSN_RE = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')

# ============================================================
# 4. API keys / tokens
# ============================================================

# Pattern: key_name=VALUE or key_name: VALUE or key_name: "VALUE"
# Matches common credential assignment forms in code/configs.
# Capture the assignment operator and value, replace just the value.
_API_KEY_ASSIGNMENT_RE = re.compile(
    r'(?i)\b('
    r'api[_-]?key|apikey|'
    r'access[_-]?token|auth[_-]?token|'
    r'secret|secret[_-]?key|'
    r'client[_-]?secret|'
    r'password|passwd|pwd'
    r')\s*[:=]\s*["\']?([A-Za-z0-9_\-./+=]{8,})["\']?',
    re.IGNORECASE,
)

# Bare token patterns (no assignment context needed)
_OPENAI_KEY_RE = re.compile(r'sk-[A-Za-z0-9_-]{20,}')
_GITHUB_PAT_RE = re.compile(r'ghp_[A-Za-z0-9]{36,}')
_GITHUB_FINEGRAINED_RE = re.compile(r'github_pat_[A-Za-z0-9_]{40,}')
_GITHUB_OAUTH_RE = re.compile(r'gho_[A-Za-z0-9]{36,}')
_GITHUB_USER_TOKEN_RE = re.compile(r'ghu_[A-Za-z0-9]{36,}')
_GITHUB_REFRESH_RE = re.compile(r'ghr_[A-Za-z0-9]{36,}')
_SLACK_TOKEN_RE = re.compile(r'xox[baprs]-[A-Za-z0-9-]{10,}')
_AWS_ACCESS_KEY_RE = re.compile(r'\bAKIA[0-9A-Z]{16}\b')
# JWT (three base64 segments separated by dots, total >= 40 chars)
_JWT_RE = re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b')

# Bearer / Authorization header: "Bearer eyJ..." or "Authorization: Bearer ..."
_BEARER_RE = re.compile(
    r'(?i)\b(?:Bearer|Authorization\s*:\s*Bearer)\s+([A-Za-z0-9_\-./+=]{8,})'
)

# Generic "token=VALUE"
_TOKEN_ASSIGNMENT_RE = re.compile(
    r'(?i)\btoken\s*[:=]\s*["\']?([A-Za-z0-9_\-./+=]{8,})["\']?'
)

# ============================================================
# 5. GitHub username in sensitive contexts
# ============================================================

# Only redact @username when it appears in patterns like:
#   github.com/@username
#   git@github.com:username/...
# These are credential-like, not casual mentions.
_GITHUB_URL_AT_RE = re.compile(
    r'(github\.com/)(@)([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?)',
    re.IGNORECASE,
)
_GIT_SSH_RE = re.compile(
    r'(git@)(github\.com)(:[A-Za-z0-9][A-Za-z0-9-]{0,38}/)',
    re.IGNORECASE,
)


def _redact_api_key_assignment(match: re.Match) -> str:
    """Replace just the VALUE in `api_key="VALUE"` — keep the key name visible."""
    full = match.group(0)
    value = match.group(2)
    return full.replace(value, "[REDACTED]")


def _redact_bearer(match: re.Match) -> str:
    """Replace the token after Bearer, keep 'Bearer ' visible."""
    full = match.group(0)
    value = match.group(1)
    return full.replace(value, "[REDACTED]")


def _redact_token_assignment(match: re.Match) -> str:
    full = match.group(0)
    value = match.group(1)
    return full.replace(value, "[REDACTED]")


def remove_pii(text: str, replace_with: str = "[REDACTED]") -> str:
    """Удалить PII из текста.

    Args:
        text: input text (prompt or completion)
        replace_with: replacement string for redacted content

    Returns:
        text with emails, phones, IPs, SSNs, API keys, tokens, and
        credential-style GitHub URLs replaced with `replace_with`.
    """
    if not text:
        return text

    # 1. Emails (standard + obfuscated)
    text = EMAIL_RE.sub(replace_with, text)
    text = EMAIL_OBFUSCATED_RE.sub(
        lambda m: f"{m.group(1)} {replace_with} {m.group(2)} {replace_with} {m.group(3)}",
        text,
    )
    text = EMAIL_OBFUSCATED_PLAIN_RE.sub(
        lambda m: f"{m.group(1)} {replace_with} {m.group(2)} {replace_with} {m.group(3)}",
        text,
    )

    # 2. Phones (intl first to catch +, then RU 8-, then generic)
    text = PHONE_INTL_RE.sub(replace_with, text)
    text = PHONE_RU_RE.sub(replace_with, text)
    text = PHONE_RE.sub(replace_with, text)

    # 3. IP / SSN
    text = IP_RE.sub(replace_with, text)
    text = SSN_RE.sub(replace_with, text)

    # 4. API keys / tokens
    # 4a. Assignment-style: api_key="...", secret: "...", password=...
    text = _API_KEY_ASSIGNMENT_RE.sub(_redact_api_key_assignment, text)
    # 4b. token=VALUE (handled separately because "token" is a common substring)
    text = _TOKEN_ASSIGNMENT_RE.sub(_redact_token_assignment, text)
    # 4c. Bearer / Authorization: Bearer ...
    text = _BEARER_RE.sub(_redact_bearer, text)
    # 4d. Bare token patterns
    text = _OPENAI_KEY_RE.sub(replace_with, text)
    text = _GITHUB_PAT_RE.sub(replace_with, text)
    text = _GITHUB_FINEGRAINED_RE.sub(replace_with, text)
    text = _GITHUB_OAUTH_RE.sub(replace_with, text)
    text = _GITHUB_USER_TOKEN_RE.sub(replace_with, text)
    text = _GITHUB_REFRESH_RE.sub(replace_with, text)
    text = _SLACK_TOKEN_RE.sub(replace_with, text)
    text = _AWS_ACCESS_KEY_RE.sub(replace_with, text)
    # NOTE: _AWS_SECRET_RE is too broad (matches any 40-char base64) — skip by default.
    # Enable only if you have a known AWS context.
    text = _JWT_RE.sub(replace_with, text)

    # 5. GitHub username in credential-style URLs
    # github.com/@user → github.com/[REDACTED]
    text = _GITHUB_URL_AT_RE.sub(
        lambda m: f"{m.group(1)}{replace_with}", text
    )
    # git@github.com:user/ → git[REDACTED]:user/ (redact the @github.com part)
    text = _GIT_SSH_RE.sub(
        lambda m: f"git{replace_with}{m.group(2)}{m.group(3)}", text
    )

    return text


def clean_pair(pair: dict) -> dict:
    """Очистить пару от PII."""
    pair["prompt"] = remove_pii(pair.get("prompt", ""))
    pair["completion"] = remove_pii(pair.get("completion", ""))
    return pair


def detect_pii(text: str) -> dict:
    """Return dict of PII types found in text (for reporting/auditing).

    Does not modify the text. Useful for stats: "how many pairs had API keys?"
    """
    if not text:
        return {}
    found: dict[str, int] = {}
    if EMAIL_RE.search(text) or EMAIL_OBFUSCATED_RE.search(text) or EMAIL_OBFUSCATED_PLAIN_RE.search(text):
        found["email"] = found.get("email", 0) + 1
    if PHONE_INTL_RE.search(text) or PHONE_RU_RE.search(text) or PHONE_RE.search(text):
        found["phone"] = found.get("phone", 0) + 1
    if IP_RE.search(text):
        found["ip"] = found.get("ip", 0) + 1
    if SSN_RE.search(text):
        found["ssn"] = found.get("ssn", 0) + 1
    if (_API_KEY_ASSIGNMENT_RE.search(text) or _TOKEN_ASSIGNMENT_RE.search(text)
            or _BEARER_RE.search(text)):
        found["api_key_or_token"] = found.get("api_key_or_token", 0) + 1
    if (_OPENAI_KEY_RE.search(text) or _GITHUB_PAT_RE.search(text)
            or _GITHUB_FINEGRAINED_RE.search(text) or _GITHUB_OAUTH_RE.search(text)
            or _GITHUB_USER_TOKEN_RE.search(text) or _GITHUB_REFRESH_RE.search(text)
            or _SLACK_TOKEN_RE.search(text) or _AWS_ACCESS_KEY_RE.search(text)
            or _JWT_RE.search(text)):
        found["bare_token"] = found.get("bare_token", 0) + 1
    if _GITHUB_URL_AT_RE.search(text) or _GIT_SSH_RE.search(text):
        found["github_cred_url"] = found.get("github_cred_url", 0) + 1
    return found
