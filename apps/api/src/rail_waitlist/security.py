from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class SecretBox:
    def __init__(self, key: bytes | None = None) -> None:
        self._fernet = Fernet(key or get_settings().encryption_key())

    def encrypt_dict(self, value: dict[str, Any]) -> str:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(payload).decode("ascii")

    def decrypt_dict(self, value: str) -> dict[str, Any]:
        try:
            payload = self._fernet.decrypt(value.encode("ascii"))
        except InvalidToken as error:
            raise RuntimeError("stored secret cannot be decrypted") from error
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("stored secret payload is invalid") from error
        if not isinstance(decoded, dict):
            raise RuntimeError("stored secret payload is invalid")
        return decoded


secret_box = SecretBox()
