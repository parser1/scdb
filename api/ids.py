"""Identifiers, time, and secret redaction."""

from __future__ import annotations

import secrets
import time
import uuid
from datetime import datetime, timezone

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    millis = int(time.time() * 1000)
    time_part = ""
    for _ in range(10):
        time_part = _ALPHABET[millis & 31] + time_part
        millis >>= 5
    rand = uuid.uuid4().hex
    rand_part = ""
    value = int(rand, 16)
    for _ in range(16):
        rand_part = _ALPHABET[value & 31] + rand_part
        value >>= 5
    return f"{prefix}{time_part}{rand_part}"


def token(nbytes: int = 18) -> str:
    return secrets.token_urlsafe(nbytes)


SECRET_FIELD_NAMES = {
    "password",
    "cql_secret",
    "manager_secret",
    "manager_agent_token",
    "grafana_password",
    "api_key",
    "key",
    "authorization",
    "ssh_public_key",
    "custom_data",
    "customdata",
}


def redact(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in SECRET_FIELD_NAMES:
                cleaned[key] = "<redacted>"
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def redact_text(text: str, secrets_in_text: list[str] | None = None) -> str:
    cleaned = text
    for secret in secrets_in_text or []:
        if secret:
            cleaned = cleaned.replace(secret, "<redacted>")
    lines = []
    for line in cleaned.splitlines():
        lower = line.lower()
        if "password" in lower or "auth_token" in lower or "api_key" in lower:
            if "=" in line:
                key, _sep, _val = line.partition("=")
                lines.append(f"{key}=<redacted>")
                continue
        lines.append(line)
    return "\n".join(lines)
