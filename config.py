# -*- coding: utf-8 -*-
"""إعدادات البوت - كل القيم من متغيرات البيئة (Railway)"""
import os

def get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

BOT_TOKEN = get_env("BOT_TOKEN")
ADMIN_ID = int(get_env("ADMIN_ID") or "0")
ADMIN_USERNAME = get_env("ADMIN_USERNAME", "Reozaki_94")
ADMIN_PHONE = get_env("ADMIN_PHONE", "+9647733921468")
DATABASE_URL = get_env("DATABASE_URL")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN مطلوب. أضفه في Railway أو في ملف .env")
