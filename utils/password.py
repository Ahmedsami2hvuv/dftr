# -*- coding: utf-8 -*-
"""تشفير كلمة المرور والتحقق منها"""
import hashlib

_SALT = "dftr_ledger_bot_2025"


def hash_password(password: str) -> str:
    return hashlib.sha256((_SALT + (password or "").strip()).encode("utf-8")).hexdigest()


def check_password(password: str, password_hash: str) -> bool:
    return password_hash == hash_password(password)
