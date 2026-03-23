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


def _url_looks_local(u: str) -> bool:
    ul = (u or "").lower()
    return "localhost" in ul or "127.0.0.1" in ul or ul.startswith("http://0.") or "[::1]" in ul


def public_web_base_url_for_telegram_fetch() -> str | None:
    """
    قاعدة https://… يمكن لخوادم تيليجرام جلب صورة من /creditbook/photo/…
    إذا كان WEB_BASE_URL ما زال localhost لكن المنصة تعرّف دوميناً عاماً (مثل Railway) نستخدمه.
    يمكن ضبط TELEGRAM_PHOTO_BASE_URL أو PUBLIC_WEB_BASE_URL يدوياً.
    """
    manual = get_env("TELEGRAM_PHOTO_BASE_URL") or get_env("PUBLIC_WEB_BASE_URL")
    if manual and manual.startswith(("http://", "https://")) and not _url_looks_local(manual):
        return manual.rstrip("/")

    if WEB_BASE_URL and not _url_looks_local(WEB_BASE_URL):
        return WEB_BASE_URL.rstrip("/")

    rd = get_env("RAILWAY_PUBLIC_DOMAIN")
    if rd:
        h = rd.strip().lstrip("https://").lstrip("http://").split("/")[0]
        if h:
            return f"https://{h}".rstrip("/")

    rpu = get_env("RAILWAY_PUBLIC_URL")
    if rpu and rpu.startswith(("http://", "https://")) and not _url_looks_local(rpu):
        return rpu.rstrip("/")

    rex = get_env("RENDER_EXTERNAL_URL")
    if rex and rex.startswith(("http://", "https://")) and not _url_looks_local(rex):
        return rex.rstrip("/")

    return None


# بطاقة «صنع بواسطة» في واجهة الويب — هوية صانع الدفتر (ثابتة لجميع المستخدمين؛ يمكن تغييرها من Railway)
CREDITBOOK_SHOWCASE_NAME = get_env("CREDITBOOK_SHOWCASE_NAME", "ابو الاكبر للتوصيل")
CREDITBOOK_SHOWCASE_PHONE = get_env("CREDITBOOK_SHOWCASE_PHONE", "07733921468")

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


def customer_share_message_footer() -> str:
    """
    نص يُلحق بنهاية رسالة مشاركة الفاتورة/الرصيد (واتساب ومعاينة الموقع والبوت).
    """
    bot_u = (BOT_USERNAME or "").strip().lstrip("@")
    bot_line = f"https://t.me/{bot_u}" if bot_u else "— (لم يُضبط BOT_USERNAME)"

    base = (WEB_BASE_URL or "").strip().rstrip("/")
    if base and not base.startswith(("http://", "https://")):
        base = "https://" + base.lstrip("/")
    web_line = f"{base}/creditbook" if base else "— (لم يُضبط WEB_BASE_URL)"

    return (
        "ــــــــــــــــــــــــ\n"
        "هذه الفاتورة تم إنشاؤها عبر بوت دفتر الديون.\n\n"
        "هل تود فتح دفتر لك؟ انقر على الرابط:\n"
        f"{bot_line}\n\n"
        "إن لم تكن تملك تليجرام انقر على الرابط:\n"
        f"{web_line}"
    )
