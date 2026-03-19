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
WEB_PORT = int(get_env("WEB_PORT") or "8000")

# رابط الموقع العام:
# 1) WEB_BASE_URL (يدوي)
# 2) RAILWAY_PUBLIC_DOMAIN / RAILWAY_STATIC_URL (تلقائي في Railway)
# 3) localhost (fallback محلي للتطوير فقط)
_raw_base = get_env("WEB_BASE_URL")
if not _raw_base:
    _railway_domain = get_env("RAILWAY_PUBLIC_DOMAIN")
    if _railway_domain:
        _raw_base = f"https://{_railway_domain.strip().lstrip('https://').lstrip('http://')}"
if not _raw_base:
    _railway_static = get_env("RAILWAY_STATIC_URL")
    if _railway_static:
        _raw_base = _railway_static
if not _raw_base:
    _raw_base = f"http://localhost:{WEB_PORT}"

WEB_BASE_URL = _raw_base.rstrip("/")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN مطلوب. أضفه في Railway أو في ملف .env")
