# -*- coding: utf-8 -*-
"""تحليل تواريخ بصيغ متعددة (يوم/شهر/سنة بأي ترتيب وفواصل)."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional


def normalize_digits(s: str) -> str:
    """أرقام عربية/فارسية → إنجليزية."""
    if not s:
        return ""
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    s = s.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    return s.strip()


def extract_int_groups(s: str) -> list[int]:
    s = normalize_digits(s)
    return [int(x) for x in re.findall(r"\d+", s) if x]


def _expand_year(y: int) -> int:
    if 100 <= y <= 9999:
        return y
    if 0 <= y < 100:
        return 2000 + y if y < 70 else 1900 + y
    return y


def _try_ymd(y: int, m: int, d: int) -> Optional[date]:
    y = _expand_year(y)
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_flexible_date(text: str) -> Optional[date]:
    """
    يقبل أمثلة: 2026-2-4، 2025/8/2، 2 8 2025، 2025\\6\\5، 4-2-2026، إلخ.
    """
    if not text or not str(text).strip():
        return None
    raw = normalize_digits(text)
    if not raw:
        return None

    # صيغة لاصقة 20260204
    digits_only = re.sub(r"\D", "", raw)
    if len(digits_only) == 8:
        try:
            y, m, d = int(digits_only[:4]), int(digits_only[4:6]), int(digits_only[6:8])
            got = _try_ymd(y, m, d)
            if got:
                return got
        except Exception:
            pass

    # وحّد الفواصل إلى -
    normalized = re.sub(r"[\s/\\.\\،]+", "-", raw)
    normalized = re.sub(r"-+", "-", normalized).strip("-")

    for fmt in (
        "%Y-%m-%d",
        "%Y-%d-%m",
        "%d-%m-%Y",
        "%m-%d-%Y",
        "%d-%m-%y",
        "%m-%d-%y",
        "%y-%m-%d",
    ):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue

    nums = extract_int_groups(raw)
    if len(nums) < 3:
        return None

    a, b, c = nums[0], nums[1], nums[2]
    candidates: list[date] = []

    def add(d0: Optional[date]):
        if d0 and d0 not in candidates:
            candidates.append(d0)

    if a >= 1000:
        add(_try_ymd(a, b, c))
        add(_try_ymd(a, c, b))
    if c >= 1000:
        add(_try_ymd(c, b, a))
        add(_try_ymd(c, a, b))
    if b >= 1000:
        add(_try_ymd(b, a, c))
        add(_try_ymd(b, c, a))

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if c >= 1000:
        d0 = _try_ymd(c, b, a)
        if d0 in candidates:
            return d0
    if a >= 1000:
        d0 = _try_ymd(a, b, c)
        if d0 in candidates:
            return d0
    return candidates[0]


def suggest_dates_near_input(text: str, max_buttons: int = 6) -> list[date]:
    """تواريخ مقترحة عند فشل التحليل."""
    today = date.today()
    nums = extract_int_groups(text or "")
    anchor = today

    years = [n for n in nums if 2000 <= n <= 2100]
    if years:
        y = years[0]
        months = [n for n in nums if 1 <= n <= 12]
        days = [n for n in nums if 1 <= n <= 31]
        months_only = [n for n in months if n not in years]
        days_only = [n for n in days if n not in years]
        if months_only and days_only:
            d0 = _try_ymd(y, months_only[0], days_only[0])
            if d0:
                anchor = d0
        else:
            try:
                anchor = date(y, today.month, min(today.day, 28))
            except ValueError:
                anchor = date(y, 1, 1)

    out: list[date] = []
    half = max_buttons // 2
    for i in range(-half, max_buttons - half):
        d = anchor + timedelta(days=i)
        if d not in out:
            out.append(d)
    return out[:max_buttons]
