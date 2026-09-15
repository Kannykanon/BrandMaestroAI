"""Encryption for stored secrets (the YouTube refresh token).

    YT_TOKEN_KEY   a Fernet key (Fernet.generate_key()); if unset, one is derived from SECRET_KEY

Deriving from SECRET_KEY means no extra setting, at the price that rotating
SECRET_KEY makes saved tokens unreadable. That only means connecting the
channel again, and decrypt() reports it clearly.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


class SecretError(RuntimeError):
    """A stored secret could not be read."""


def _fernet() -> Fernet:
    key = os.getenv("YT_TOKEN_KEY", "").strip()
    if key:
        return Fernet(key.encode())
    secret = os.getenv("SECRET_KEY", "").strip()
    if not secret:
        try:
            from Settings import settings  # reads .env the same way auth.py does
            secret = settings.secret_key.get_secret_value()
        except Exception:
            secret = ""
    if not secret:
        raise SecretError("Set YT_TOKEN_KEY or SECRET_KEY to store the YouTube sign-in")
    digest = hashlib.sha256(b"youtube-token:" + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise SecretError("The saved YouTube sign-in cannot be read (the encryption key changed); "
                          "connect the channel again") from e
