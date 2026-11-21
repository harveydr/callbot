"""Phone encryption. Full numbers stay out of logs, indexes, and API responses."""

from __future__ import annotations

import hmac
import os
import re
from hashlib import sha256

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_MOBILE = re.compile(r"1\d{10}")


class PhoneInvalidError(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    if _MOBILE.fullmatch(digits) is None:
        raise PhoneInvalidError("phone_invalid")
    return digits


def mask_phone(normalized: str) -> str:
    return f"{normalized[:3]}****{normalized[-4:]}"


def phone_hash(normalized: str, pepper: bytes) -> str:
    return hmac.new(pepper, normalized.encode("utf-8"), sha256).hexdigest()


def encrypt_secret(plain: str, key: bytes) -> bytes:
    aes = AESGCM(key)
    nonce = os.urandom(12)
    return nonce + aes.encrypt(nonce, plain.encode("utf-8"), None)


def encrypt_phone(normalized: str, key: bytes) -> bytes:
    aes = AESGCM(key)
    nonce = os.urandom(12)
    return nonce + aes.encrypt(nonce, normalized.encode("utf-8"), None)


def decrypt_phone(blob: bytes, key: bytes) -> str:
    aes = AESGCM(key)
    nonce, ciphertext = blob[:12], blob[12:]
    return aes.decrypt(nonce, ciphertext, None).decode("utf-8")
