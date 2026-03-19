# -*- coding: utf-8 -*-
"""تطبيع أرقام الهاتف (عراق +964) مع قبول أي شكل شائع للإدخال."""
from __future__ import annotations

import re
import unicodedata


# علامات اتجاه/خفية يضيفها تليجرام أو لوحات المفاتيح حول الأرقام
_INVISIBLE = (
    "\u200e",  # LRM
    "\u200f",  # RLM
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
    "\u2060",  # word joiner
    "\u2066",  # isolate LTR
    "\u2067",  # isolate RTL
    "\u2068",  # first strong isolate
    "\u2069",  # pop isolate
    "\ufeff",  # BOM
    "\xa0",  # nbsp
)


def _strip_invisible(s: str) -> str:
    for ch in _INVISIBLE:
        s = s.replace(ch, "")
    return s


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s)


def normalize_phone(phone: str) -> str:
    """
    يحوّل الرقم إلى شكل موحّد +9647XXXXXXXXX قدر الإمكان.
    يقبل: ⁦+964 777 363 0152 ، 777 363 0152 ، 07773630152 ، 964777... ، 00964...
    """
    if phone is None:
        return ""
    s = unicodedata.normalize("NFKC", str(phone))
    s = _strip_invisible(s).strip()
    if not s:
        return ""

    d = _digits_only(s)
    if not d:
        return ""

    # بادئة 00 دولية
    if d.startswith("00"):
        d = d[2:]

    # العراق: 964 + جزء محلي (قد يأتي مع 0 زائد بعد 964)
    if d.startswith("964"):
        rest = d[3:]
        rest = rest.lstrip("0") or rest
        return ("+964" + rest) if rest else ""

    # محلي بصيغة 07XXXXXXXXX
    if d.startswith("0") and len(d) > 1:
        d = d[1:]

    # لا توثر lstrip('0') على الأرقام التي تبدأ بـ 7 (مثل 777...)
    # فقط أصفار حقيقية في البداية مثل 00777...
    d = d.lstrip("0") or d
    if not d:
        return ""

    # غالباً موبايل عراقي: 7 + 9 أرقام (وأحياناً اختلاف بسيط في الطول)
    if d[0] == "7" and 9 <= len(d) <= 11:
        return "+964" + d

    # أرقام محلية طويلة بدون 7 (نادرة) — نضيف 964
    if 9 <= len(d) <= 11:
        return "+964" + d

    return "+" + d


def is_plausible_iraq_mobile(normalized: str) -> bool:
    """التحقق المبدئي من طول رقم عراقي بعد التطبيع (+964 + 9 أرقام على الأقل)."""
    d = _digits_only(normalize_phone(normalized))
    return bool(d.startswith("964") and len(d) >= 12)


def same_phone(a: str, b: str) -> bool:
    """هل يمثل الرّقمان نفس الخطّ (بعد التطبيع ومقارنة الأرقام فقط)."""
    da = _digits_only(normalize_phone(a))
    db_ = _digits_only(normalize_phone(b))
    return bool(da) and da == db_


def wa_number(phone: str) -> str:
    """رقم للاستخدام في wa.me / api.whatsapp.com (أرقام فقط بدون +)."""
    return normalize_phone(phone).lstrip("+")


def phone_local_display(phone: str) -> str:
    """للعرض: +964 7xx xxx xxxx تقريباً."""
    n = normalize_phone(phone)
    if not n.startswith("+964") or len(n) < 6:
        return n
    rest = n[4:]
    if len(rest) == 10:
        return f"+964 {rest[:3]} {rest[3:6]} {rest[6:]}"
    return n


def format_phone_iq_local_display(phone: str) -> str:
    """
    للعرض على الموقع: بدون +964 — صيغة محلية 07XXXXXXXXX (11 رقم).
    يصلح أرقاماً طويلة/مكررة في قاعدة البيانات بأخذ 10 أرقام وطنية بعد 964.
    """
    if not (phone or "").strip():
        return ""
    d = _digits_only(normalize_phone(phone))
    if not d:
        return (phone or "").strip()

    if d.startswith("964"):
        rest = d[3:]
        rest = rest.lstrip("0") or rest
        while rest.startswith("964"):
            rest = rest[3:].lstrip("0")
        if len(rest) > 10:
            rest = rest[:10]
        if len(rest) == 10 and rest[0] == "7":
            return "0" + rest
        if len(rest) == 9 and rest[0] == "7":
            return "0" + rest

    if d.startswith("0") and len(d) == 11 and d[1] == "7":
        return d
    if len(d) == 10 and d[0] == "7":
        return "0" + d

    return (phone or "").strip()
