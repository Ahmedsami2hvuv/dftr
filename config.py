# -*- coding: utf-8 -*-
"""إعدادات البوت - كل القيم من متغيرات البيئة (Railway)"""
import os
from pathlib import Path

def get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

BOT_TOKEN = get_env("BOT_TOKEN")
ADMIN_ID = int(get_env("ADMIN_ID") or "0")
ADMIN_USERNAME = get_env("ADMIN_USERNAME", "Reozaki_94")
ADMIN_PHONE = get_env("ADMIN_PHONE", "+9647733921468")
BOT_USERNAME = get_env("BOT_USERNAME", "Dftr1_bot")
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

# إذا المستخدم كتب الدومين بدون بروتوكول، نضيف https تلقائياً
if _raw_base and not _raw_base.startswith(("http://", "https://")):
    _raw_base = "https://" + _raw_base.lstrip("/")

WEB_BASE_URL = _raw_base.rstrip("/")

# صور المعاملات المرفوعة من موقع الويب (ليست من تيليجرام)
_ROOT_DIR = Path(__file__).resolve().parent
WEB_TX_UPLOAD_DIR = _ROOT_DIR / "data" / "web_tx_photos"

# شعار صفحة «مشاركة الديون» على الويب: صورة PNG بصيغة Base64 (الصق القيمة كاملة في Railway)
# يمكن لصق النص كما هو من ملف .b64 أو بصيغة data:image/png;base64,.... — بدون رفع ملف للريبو
# إن تُرك فارغاً يُستخدم ملف static/ إن وُجد، وإلا أيقونة SVG افتراضية
BOT_LOGO_BASE64 = (os.environ.get("BOT_LOGO_BASE64") or os.environ.get("WEB_BRAND_LOGO_B64") or "").strip()

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN مطلوب. أضفه في Railway أو في ملف .env")


def web_session_secret() -> str:
    """
    مفتاح توقيع كوكي جلسة الموقع (تسجيل الدخول عبر المتصفح).
    يُفضّل تعيين WEB_SESSION_SECRET في Railway (سلسلة عشوائية طويلة).
    """
    import hashlib

    raw = get_env("WEB_SESSION_SECRET")
    if raw:
        return raw
    return hashlib.sha256(("dftr_web_sess_v1:" + BOT_TOKEN).encode("utf-8")).hexdigest()
