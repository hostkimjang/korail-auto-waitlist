def normalize_official_train_number(value: str) -> str:
    normalized = value.strip().upper()
    if normalized.isdecimal():
        return normalized.lstrip("0") or "0"
    return normalized


def contains_protection_marker(value: str) -> bool:
    normalized = value.casefold()
    return any(
        marker in normalized
        for marker in ("-8002", "-8003", "macro_err", "captcha", "netfunnel", "blocked")
    )
