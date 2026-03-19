# -*- coding: utf-8 -*-
def normalize_phone(phone: str) -> str:
    s = (phone or "").strip().replace(" ", "").replace("-", "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if s and not s.startswith("+"):
        s = "+964" + s.lstrip("0")
    return s


def wa_number(phone: str) -> str:
    """رقم للاستخدام في wa.me (بدون +)"""
    return normalize_phone(phone).lstrip("+")
