"""Pure identity formatting shared by SRT provider integrations."""

from __future__ import annotations


def normalize_srt_train_number(value: object) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.lstrip("0") or "0"


def normalize_srt_date(value: object) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.zfill(8)[-8:]


def normalize_srt_time(value: object) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) <= 4:
        return f"{digits.zfill(4)}00"
    return digits.zfill(6)[-6:]
