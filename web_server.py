# -*- coding: utf-8 -*-
"""
موقع بسيط يعرض معلومات العميل عند فتح رابط المشاركة.
يعمل داخل نفس كونتainer البوت (Thread) بدون dependencies إضافية.
"""

from __future__ import annotations

import base64
import html
import json
import re
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

from database import SessionLocal
from app_models import BRAND_LOGO_SETTING_KEY, Customer, CustomerTransaction, ShareLink, SiteSetting, User
from config import ADMIN_ID
from config import BOT_LOGO_BASE64
from config import BOT_TOKEN
from config import BOT_USERNAME
from config import CREDITBOOK_SHOWCASE_NAME
from config import CREDITBOOK_SHOWCASE_PHONE
from config import WEB_BASE_URL
from config import WEB_TX_UPLOAD_DIR
from creditbook_web import (
    _clear_cookie_headers,
    _set_cookie_headers,
    csrf_token,
    csrf_token_public,
    csrf_verify,
    csrf_verify_public,
    get_user_from_cookie_header,
    load_all_transactions_page,
    owner_display_name_for_user,
    render_dashboard_customer_rows_html,
    REPORT_PAGE_SIZE,
    render_report_all_transactions_page,
    render_account_page,
    render_tx_history_rows_html,
    render_customer_share_page,
    render_dashboard_html,
    render_feedback_page,
    render_login_page,
    render_logout_confirm_page,
    render_customer_tx_list_fragment,
    render_owner_customer_page,
    render_register_page,
    render_tx_edit_page,
    try_login,
)
from creditbook_web_actions import (
    action_customer_create,
    action_customer_delete,
    action_customer_update,
    action_tx_delete,
    action_tx_toggle_kind,
    action_tx_update,
    action_txn_add,
    action_register_web,
    action_tx_history_dismiss,
    action_tx_history_restore,
    action_user_change_password,
    action_user_update_profile,
    build_customer_share_urls,
    is_safe_web_photo_name,
    parse_tx_datetime,
)
from utils.phone import format_phone_iq_local_display, wa_number as _wa_number

# شعار البوت (احتياطي إذا لم يُضبط BOT_LOGO_BASE64 في Railway)
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_LOGO_PATH = _STATIC_DIR / "bot_logo.png"
_LOGO_B64_PATH = _STATIC_DIR / "bot_logo.b64.txt"
_CREDITBOOK_APP_CSS = _STATIC_DIR / "creditbook_app.css"

TX_PAGE_SIZE = 15


def _mime_for_ext(fn: str) -> str:
    fn = fn.lower()
    if fn.endswith(".png"):
        return "image/png"
    if fn.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if fn.endswith(".webp"):
        return "image/webp"
    if fn.endswith(".gif"):
        return "image/gif"
    return "application/octet-stream"


def _try_local_web_photo(file_id: str) -> tuple[bytes, str] | None:
    """صورة معاملة الموقع: من القرص أو من قاعدة البيانات إن فُقد الملف على الحاوية."""
    if not file_id.startswith("web:"):
        return None
    name = file_id[4:]
    if not is_safe_web_photo_name(name):
        return None
    p = WEB_TX_UPLOAD_DIR / name
    if p.is_file():
        return p.read_bytes(), _mime_for_ext(name)
    db = SessionLocal()
    try:
        row = (
            db.query(CustomerTransaction.photo_web_blob)
            .filter(
                CustomerTransaction.photo_file_id == file_id,
                CustomerTransaction.photo_web_blob.isnot(None),
            )
            .limit(1)
            .first()
        )
        if row and row[0] is not None:
            return bytes(row[0]), _mime_for_ext(name)
    finally:
        db.close()
    return None


def _parse_multipart_post(raw: bytes, content_type: str) -> dict[str, str | tuple[bytes, str]]:
    import cgi
    import io

    fs = cgi.FieldStorage(
        fp=io.BytesIO(raw),
        environ={
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(len(raw)),
        },
        keep_blank_values=True,
    )
    out: dict[str, str | tuple[bytes, str]] = {}
    lst = getattr(fs, "list", None)
    if not lst:
        return out
    for field in lst:
        name = field.name
        if field.filename:
            data = field.file.read() if field.file else b""
            out[name] = (data, field.filename or "")
        else:
            v = field.value
            if isinstance(v, bytes):
                v = v.decode("utf-8", errors="replace")
            out[name] = v
    return out


def _request_is_secure(handler: BaseHTTPRequestHandler) -> bool:
    if WEB_BASE_URL.startswith("https"):
        return True
    return (handler.headers.get("X-Forwarded-Proto") or "").lower() == "https"


def _send_html_page(
    handler: BaseHTTPRequestHandler,
    status: int,
    body: str,
    extra_headers: list[tuple[str, str]] | None = None,
) -> None:
    handler.send_response(status)
    for k, v in extra_headers or []:
        handler.send_header(k, v)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
    handler.send_header("Pragma", "no-cache")
    handler.end_headers()
    handler.wfile.write(body.encode("utf-8"))


def _redirect(
    handler: BaseHTTPRequestHandler,
    location: str,
    extra_headers: list[tuple[str, str]] | None = None,
) -> None:
    handler.send_response(302)
    handler.send_header("Location", location)
    for k, v in extra_headers or []:
        handler.send_header(k, v)
    handler.end_headers()


def _send_telegram_admin_message(text: str) -> bool:
    """إرسال نص إلى حساب الإدارة (ADMIN_ID) عبر Bot API."""
    if not BOT_TOKEN or not ADMIN_ID:
        return False
    t = (text or "").strip()
    if not t:
        return False
    payload = json.dumps(
        {"chat_id": ADMIN_ID, "text": t[:4000]},
        ensure_ascii=False,
    ).encode("utf-8")
    req = Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        return bool(data.get("ok"))
    except Exception:
        return False


def _html_escape(s: str) -> str:
    return html.escape(s or "", quote=True)


def _pwa_manifest_json_bytes() -> bytes:
    """Web App Manifest لمسار /creditbook/ (تثبيت كتطبيق)."""
    manifest = {
        "name": "دفتر الديون",
        "short_name": "دفتر الديون",
        "description": "إدارة ديون العملاء والمعاملات",
        "start_url": "/creditbook/dashboard",
        "scope": "/creditbook/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#0a0f1c",
        "theme_color": "#0891b2",
        "lang": "ar",
        "dir": "rtl",
        "icons": [
            {
                "src": "/creditbook/logo.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/creditbook/logo.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
        ],
    }
    return json.dumps(manifest, ensure_ascii=False).encode("utf-8")


def _clean_env_logo_b64(raw: str) -> str:
    """يستخرج سلسلة base64 نظيفة من المتغير أو من data:image/png;base64,..."""
    s = (raw or "").strip()
    if not s:
        return ""
    if "base64," in s:
        s = s.split("base64,", 1)[1].strip()
    # إزالة مسافات/أسطر زائدة إن لصق المستخدم نصاً مقسّماً
    return "".join(s.split())


def _guess_image_mime(data: bytes) -> str:
    if not data:
        return "image/png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _brand_visual_for_page() -> tuple[str, str]:
    """رابط الشعار — يُحمَّل من قاعدة البيانات أو المتغير أو الملف عبر هذا المسار."""
    u = "/creditbook/logo.png"
    return u, u


def _get_brand_logo_bytes_ctype() -> tuple[bytes, str]:
    """للاستجابة GET /creditbook/logo — أولاً من لوحة الأدمن (PostgreSQL)، ثم المتغير، ثم الملفات."""
    db = SessionLocal()
    try:
        row = db.query(SiteSetting).filter(SiteSetting.key == BRAND_LOGO_SETTING_KEY).first()
        if row and row.blob_value:
            data = bytes(row.blob_value)
            return data, _guess_image_mime(data)
    finally:
        db.close()
    b64 = _clean_env_logo_b64(BOT_LOGO_BASE64)
    if b64:
        try:
            return base64.standard_b64decode(b64), "image/png"
        except Exception:
            pass
    if _LOGO_B64_PATH.is_file():
        return base64.standard_b64decode(_LOGO_B64_PATH.read_text(encoding="ascii").strip()), "image/png"
    if _LOGO_PATH.is_file():
        return _LOGO_PATH.read_bytes(), "image/png"
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
        "<rect width='64' height='64' rx='14' fill='#0d9488'/>"
        "<rect x='12' y='16' width='40' height='34' rx='4' fill='#f0fdfa' stroke='#0f766e' stroke-width='2'/>"
        "</svg>"
    )
    return svg.encode("utf-8"), "image/svg+xml; charset=utf-8"


def _amount_to_str(x) -> str:
    try:
        return f"{float(x):.2f}"
    except Exception:
        return str(x)


def _kind_icon(kind: str) -> str:
    # في صفحة المشاركة نعرض من منظور العميل (عكس منظور صاحب الحساب)
    # gave => العميل أخذ => سهم للأسفل
    # took => العميل أعطى => سهم للأعلى
    return "⬇️" if kind == "gave" else "⬆️"


def _render_page(token: str, offset: int) -> str:
    db = SessionLocal()
    try:
        link = db.query(ShareLink).filter(ShareLink.token == token).first()
        if not link:
            return "<h3>رابط غير صالح</h3>"

        if link.expires_at:
            # link.expires_at being naive datetime.utcnow compatible
            import datetime as _dt

            if link.expires_at < _dt.datetime.utcnow():
                return "<h3>الرابط منتهي الصلاحية</h3>"

        cust = link.customer
        # balance: gave - took
        gave = sum(t.amount for t in cust.transactions if t.kind == "gave")
        took = sum(t.amount for t in cust.transactions if t.kind == "took")
        bal = float(gave - took)

        total = (
            db.query(CustomerTransaction)
            .filter(CustomerTransaction.customer_id == cust.id)
            .count()
        )

        txs = (
            db.query(CustomerTransaction)
            .filter(CustomerTransaction.customer_id == cust.id)
            .order_by(CustomerTransaction.created_at.desc())
            .offset(offset)
            .limit(TX_PAGE_SIZE)
            .all()
        )

        # الرصيد الجاري لكل معاملة (بعد تنفيذ هذه المعاملة)
        all_txs_asc = (
            db.query(CustomerTransaction)
            .filter(CustomerTransaction.customer_id == cust.id)
            .order_by(CustomerTransaction.created_at.asc(), CustomerTransaction.id.asc())
            .all()
        )
        running = 0.0
        running_after_by_tx = {}
        for rt in all_txs_asc:
            amt = float(rt.amount or 0)
            if rt.kind == "gave":
                running += amt
            else:
                running -= amt
            running_after_by_tx[rt.id] = running

        has_more = offset + TX_PAGE_SIZE < total
        more_offset = offset + TX_PAGE_SIZE

        tx_rows = []
        for t in txs:
            dt = t.created_at.strftime("%Y-%m-%d %H:%M")
            note = (t.note or "").strip()
            note_html = f"<div class='note'>ملاحظة: {note}</div>" if note else "<div class='note'>ملاحظة: —</div>"
            photo_html = ""
            if getattr(t, "photo_file_id", None):
                fid = quote(str(t.photo_file_id), safe="")
                photo_html = (
                    f"<div class='photo-wrap'><a href='/creditbook/photo-view/{fid}' target='_blank' rel='noopener'>"
                    f"<img class='photo' src='/creditbook/photo/{fid}' alt='صورة المعاملة'/></a></div>"
                )
            remain = running_after_by_tx.get(t.id, bal)
            # منظور العميل: رصيد موجب = عليه دين → أحمر؛ سالب = لصالحه → أخضر
            remain_class = "bal-red" if remain > 0 else ("bal-green" if remain < 0 else "")
            # أعطيت(صاحب الدفتر)=took → للعميل «أعطيت» أخضر | أخذت=gave → للعميل «أخذت» أحمر (ذمة/مسؤولية)
            tx_kind_class = "bal-red" if t.kind == "gave" else "bal-green"
            tx_rows.append(
                f"""
                <div class='tx'>
                  <div class='top'>{dt}</div>
                  <div class='tx-content'>
                    <div class='tx-text'>
                      <div class='remain {remain_class}'>الرصيد الحالي: {_amount_to_str(remain)} د.ع.</div>
                      <div class='main {tx_kind_class}'>{_kind_icon(t.kind)} {_kind_label(t.kind)} - {_amount_to_str(t.amount)} د.ع.</div>
                      {note_html}
                    </div>
                    {photo_html}
                  </div>
                </div>
                """
            )

        more_btn = ""
        if has_more:
            # offset button keep same token
            more_btn = f"<a class='btn' href='/creditbook/balance/{token}?lang=ar&offset={more_offset}'>➕ عرض الباقيات</a>"

        balance_text = "الرصيد الحالي: "
        if bal > 0:
            balance_text += f"{bal:.2f}"
        elif bal < 0:
            balance_text += f"{abs(bal):.2f}"
        else:
            balance_text += "0"
        # منظور العميل في الصفحة: موجب = مديون لصاحب الدفتر → أحمر
        balance_class = "bal-red" if bal > 0 else ("bal-green" if bal < 0 else "")

        owner = cust.user
        owner_name = owner_display_name_for_user(owner, empty="صاحب الحساب") if owner else "صاحب الحساب"
        owner_phone = (owner.phone or "").strip() if owner else ""

        wa_btn = ""
        if owner_phone:
            wa_phone = _wa_number(owner_phone)
            wa_btn = (
                f"<a class='btn wa' href='https://api.whatsapp.com/send?phone={wa_phone}' "
                f"target='_blank' rel='noopener'>راسل {owner_name}</a>"
            )

        bot_btn = ""
        if BOT_USERNAME:
            bot_btn = f"<a class='btn bot' href='https://t.me/{BOT_USERNAME}' target='_blank' rel='noopener'>افتحلك دفتر</a>"

        title = "دفتر الديون"
        brand_img_src, favicon_href = _brand_visual_for_page()
        # بطاقة «صنع بواسطة» — صانع الدفتر (ثابت)، وليس صاحب حساب التاجر
        showcase_name_esc = _html_escape(CREDITBOOK_SHOWCASE_NAME)
        raw_show = (CREDITBOOK_SHOWCASE_PHONE or "").strip()
        if raw_show:
            showcase_disp = format_phone_iq_local_display(raw_show) or raw_show
            show_wa = _wa_number(raw_show)
            wa_href_show = _html_escape(f"https://api.whatsapp.com/send?phone={show_wa}")
            showcase_phone_html = (
                f"<a class='owner-phone owner-phone-wa creditbook-showcase-phone' href='{wa_href_show}' "
                f"target='_blank' rel='noopener' dir='ltr' title='فتح واتساب'>{_html_escape(showcase_disp)}</a>"
            )
        else:
            showcase_phone_html = "<span class='owner-phone-muted'>لم يُضبط رقم صانع الدفتر</span>"
        cust_disp_phone = format_phone_iq_local_display((cust.phone or "").strip()) if cust.phone else ""
        cust_meta = _html_escape(cust.name) + (
            f" — {_html_escape(cust_disp_phone)}" if cust_disp_phone else ""
        )
        return f"""
        <!doctype html>
        <html lang='ar' dir='rtl'>
          <head>
            <meta charset='utf-8'/>
            <meta name='viewport' content='width=device-width, initial-scale=1'/>
            <link rel='icon' href='{_html_escape(favicon_href)}' type='image/png'/>
            <link rel='preconnect' href='https://fonts.googleapis.com'/>
            <link rel='preconnect' href='https://fonts.gstatic.com' crossorigin/>
            <link href='https://fonts.googleapis.com/css2?family=Tajawal:wght@400;600;700;800;900&display=swap' rel='stylesheet'/>
            <title>{title}</title>
            <style>
              body {{
                font-family: 'Tajawal', system-ui, sans-serif;
                color: #e8eef7;
                background: #04060d;
                background-image:
                  radial-gradient(ellipse 100% 70% at 50% -15%, rgba(34, 211, 238, 0.14), transparent 52%),
                  radial-gradient(ellipse 70% 45% at 95% 30%, rgba(167, 139, 250, 0.08), transparent 45%),
                  linear-gradient(180deg, #060912 0%, #0a0f1a 100%);
                padding: 16px;
                margin: 0;
                min-height: 100vh;
                box-sizing: border-box;
              }}
              .card {{
                background: rgba(15, 23, 42, 0.58);
                backdrop-filter: blur(16px);
                -webkit-backdrop-filter: blur(16px);
                border-radius: 20px;
                padding: 20px;
                box-shadow: 0 12px 40px rgba(0,0,0,0.45), 0 0 0 1px rgba(56, 189, 248, 0.22), inset 0 1px 0 rgba(255,255,255,0.06);
                border: 1px solid rgba(56, 189, 248, 0.2);
              }}
              /* في RTL: يمين = الشعار والعنوان، يسار = بطاقة صغيرة «صنع بواسطة» */
              .brand-header {{
                display: flex;
                flex-direction: row;
                justify-content: space-between;
                align-items: center;
                gap: 12px;
                margin-bottom: 14px;
                flex-wrap: wrap;
              }}
              .brand {{
                display: flex;
                flex-direction: row;
                align-items: center;
                gap: 14px;
                flex: 0 1 auto;
              }}
              .brand h2 {{
                margin: 0;
                font-size: clamp(1.35rem, 4vw, 1.95rem);
                font-weight: 900;
                line-height: 1.2;
                letter-spacing: -0.02em;
                background: linear-gradient(120deg, #67e8f9 0%, #22d3ee 35%, #a78bfa 72%, #c4b5fd 100%);
                -webkit-background-clip: text;
                background-clip: text;
                -webkit-text-fill-color: transparent;
                filter: drop-shadow(0 0 20px rgba(34, 211, 238, 0.25));
              }}
              .brand-logo {{
                width: 64px;
                height: 64px;
                border-radius: 18px;
                object-fit: cover;
                flex-shrink: 0;
                box-shadow: 0 0 28px rgba(34, 211, 238, 0.2), 0 0 0 2px rgba(167, 139, 250, 0.35);
                border: 2px solid rgba(255,255,255,0.12);
              }}
              /* افتراضي = موبايل: صف واحد للاسم + الرقم، صندوق مضغوط */
              .owner-showcase {{
                flex: 0 1 auto;
                max-width: min(100%, 92vw);
                min-width: 0;
                background: linear-gradient(145deg, #0e7490 0%, #0891b2 38%, #6366f1 100%);
                color: #fff;
                padding: 8px 10px;
                border-radius: 12px;
                border: 1px solid rgba(167, 139, 250, 0.45);
                box-shadow:
                  0 8px 28px rgba(34, 211, 238, 0.25),
                  inset 0 1px 0 rgba(255,255,255,0.15),
                  0 0 0 1px rgba(34, 211, 238, 0.2);
                position: relative;
                overflow: hidden;
              }}
              .owner-showcase::before {{
                content: "";
                position: absolute;
                top: -30%;
                inset-inline-end: -20%;
                width: 80px;
                height: 80px;
                background: radial-gradient(circle, rgba(255,255,255,0.14) 0%, transparent 70%);
                pointer-events: none;
              }}
              .owner-showcase::after {{
                display: none;
              }}
              .owner-badge {{
                font-size: 0.8rem;
                font-weight: 800;
                letter-spacing: 0.06em;
                opacity: 0.92;
                margin-bottom: 6px;
                position: relative;
                z-index: 1;
              }}
              .owner-name-row {{
                display: flex;
                flex-direction: row;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
                flex-wrap: nowrap;
                position: relative;
                z-index: 1;
              }}
              .owner-name {{
                font-size: clamp(1.1rem, 4.3vw, 1.35rem);
                font-weight: 800;
                line-height: 1.25;
                margin: 0;
                text-shadow: 0 1px 6px rgba(0,0,0,0.12);
                flex: 1 1 auto;
                min-width: 0;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
              }}
              .owner-phone {{
                display: inline-flex;
                align-items: center;
                gap: 7px;
                font-size: 0.9rem;
                font-weight: 700;
                color: #ecfdf5 !important;
                text-decoration: none;
                padding: 6px 11px;
                background: rgba(0,0,0,0.22);
                border-radius: 999px;
                border: 1px solid rgba(254, 202, 202, 0.55);
                flex-shrink: 0;
                white-space: nowrap;
                transition: transform .15s ease, background .15s ease, box-shadow .15s ease;
                position: relative;
                z-index: 1;
                box-shadow:
                  0 0 10px rgba(239, 68, 68, 0.95),
                  0 0 22px rgba(220, 38, 38, 0.65),
                  0 2px 8px rgba(127, 29, 29, 0.45);
              }}
              .owner-phone:hover {{
                background: rgba(0,0,0,0.25);
                transform: scale(1.02);
              }}
              .owner-showcase a.owner-phone-wa.creditbook-showcase-phone {{
                box-shadow:
                  0 0 10px rgba(239, 68, 68, 0.32),
                  0 0 20px rgba(248, 113, 113, 0.22) !important;
                background: rgba(254, 226, 226, 0.22) !important;
                border-color: rgba(252, 165, 165, 0.5);
                text-shadow: 0 0 8px rgba(254, 202, 202, 0.75), 0 0 16px rgba(239, 68, 68, 0.28);
              }}
              .owner-phone-wa::before {{
                content: "";
                display: inline-block;
                width: 18px;
                height: 18px;
                background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23fff'%3E%3Cpath d='M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.435 9.884-9.881 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z'/%3E%3C/svg%3E") center/contain no-repeat;
                flex-shrink: 0;
                opacity: 0.95;
              }}
              .owner-phone-muted {{
                font-size: 0.95rem;
                opacity: 0.85;
                font-weight: 600;
                position: relative;
                z-index: 1;
              }}
              .balance {{ font-size: 18px; margin: 8px 0 16px 0; font-weight: 700; }}
              .bal-red {{ color: #f87171; }}
              .bal-green {{ color: #4ade80; }}
              .tx {{ background: rgba(15, 23, 42, 0.45); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 12px; padding: 12px; margin: 10px 0; }}
              .top {{ color: #94a3b8; font-size: 12px; }}
              .tx-content {{ margin-top: 6px; display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }}
              .tx-text {{ flex: 1; min-width: 0; }}
              .main {{ margin-top: 6px; font-size: 15px; font-weight: 700; }}
              .remain {{ margin-top: 6px; font-size: 13px; }}
              .remain.bal-red {{ color: #f87171; }}
              .remain.bal-green {{ color: #4ade80; }}
              .note {{ margin-top: 6px; color: #94a3b8; font-size: 13px; }}
              .photo-wrap {{ flex: 0 0 auto; margin-top: 2px; }}
              .photo {{ width: 56px; height: 56px; object-fit: cover; border-radius: 8px; border: 1px solid rgba(148, 163, 184, 0.25); cursor: pointer; }}
              .btn {{
                display: inline-block;
                padding: 10px 18px;
                color: #fff;
                text-decoration: none;
                border-radius: 999px;
                margin-top: 10px;
                font-weight: 700;
                font-size: 14px;
                box-shadow: 0 4px 18px rgba(0,0,0,.35);
                transition: transform .15s ease, box-shadow .2s ease, filter .15s ease;
              }}
              .btn:hover {{
                transform: translateY(-2px);
                box-shadow: 0 0 28px rgba(34, 211, 238, 0.2), 0 8px 24px rgba(0,0,0,.35);
                filter: brightness(1.06);
              }}
              .wa {{ background: linear-gradient(135deg, #25d366, #059669); margin-inline-start: 8px; box-shadow: 0 4px 20px rgba(34, 197, 94, 0.35); }}
              .bot {{ background: linear-gradient(135deg, #6366f1, #4f46e5); box-shadow: 0 4px 20px rgba(99, 102, 241, 0.35); }}
              .meta {{ margin-bottom: 10px; line-height: 1.5; }}
              .customer-line {{ font-size: 15px; color: #cbd5e1; font-weight: 600; }}
              .actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }}
              /* حاسوب: بطاقة أكبر، الاسم فوق والرقم تحت */
              @media (min-width: 561px) {{
                .owner-showcase {{
                  max-width: min(420px, 42vw);
                  padding: 16px 20px;
                  border-radius: 16px;
                }}
                .owner-badge {{
                  font-size: 0.92rem;
                  margin-bottom: 10px;
                }}
                .owner-name-row {{
                  flex-direction: column;
                  align-items: stretch;
                  gap: 14px;
                  flex-wrap: nowrap;
                }}
                .owner-name {{
                  font-size: clamp(1.34rem, 2.05vw, 1.68rem);
                  white-space: normal;
                  overflow: visible;
                  text-overflow: clip;
                }}
                .owner-phone {{
                  font-size: 1.12rem;
                  padding: 10px 17px;
                  align-self: flex-start;
                }}
                .owner-phone-wa::before {{
                  width: 20px;
                  height: 20px;
                }}
              }}
              @media (max-width: 560px) {{
                .brand-header {{ flex-direction: column; align-items: stretch; }}
                .owner-showcase {{ order: -1; max-width: 100%; }}
                .brand {{ justify-content: center; }}
              }}
            </style>
          </head>
          <body>
            <div class='card'>
              <div class='brand-header'>
                <div class='brand'>
                  <img class='brand-logo' src="{_html_escape(brand_img_src)}" width='64' height='64' alt='دفتر الديون'/>
                  <h2>دفتر الديون</h2>
                </div>
                <div class='owner-showcase'>
                  <div class='owner-badge'>صنع بواسطة</div>
                  <div class='owner-name-row'>
                    <div class='owner-name'>{showcase_name_esc}</div>
                    {showcase_phone_html}
                  </div>
                </div>
              </div>
              <div class='meta customer-line'>👤 {cust_meta}</div>
              <div class='balance {balance_class}'>{balance_text} د.ع.</div>
              <div class='actions'>{wa_btn}{bot_btn}</div>
              {''.join(tx_rows) if tx_rows else '<p>لا توجد معاملات.</p>'}
              {more_btn}
            </div>
          </body>
        </html>
        """
    finally:
        db.close()


def _kind_label(kind: str) -> str:
    # عرض من منظور العميل (عكس منظور صاحب الحساب)
    return "أخذت" if kind == "gave" else "أعطيت"


def _resolve_telegram_file_url(file_id: str) -> str | None:
    """يحصل الرابط المباشر للصورة من Telegram getFile."""
    if not BOT_TOKEN or not file_id:
        return None
    try:
        api = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={quote(file_id, safe='')}"
        with urlopen(api, timeout=8) as r:  # nosec B310
            payload = json.loads(r.read().decode("utf-8"))
        if not payload.get("ok"):
            return None
        file_path = payload.get("result", {}).get("file_path")
        if not file_path:
            return None
        return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        qs = parse_qs(parsed.query)
        offset = 0
        if "offset" in qs:
            try:
                offset = int(qs["offset"][0])
            except Exception:
                offset = 0

        brand_img_src, favicon_href = _brand_visual_for_page()
        cookie_header = self.headers.get("Cookie")
        web_user = get_user_from_cookie_header(cookie_header)

        if path == "/creditbook/static/creditbook_app.css":
            try:
                data = _CREDITBOOK_APP_CSS.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/css; charset=utf-8")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Not found")
            return

        if path == "/creditbook/manifest.webmanifest":
            data = _pwa_manifest_json_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/creditbook/pwa-sw.js":
            try:
                sw_path = _STATIC_DIR / "pwa_sw.js"
                data = sw_path.read_bytes()
            except Exception:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/creditbook/app":
            loc = "/creditbook/dashboard" if web_user else "/creditbook/login"
            _redirect(self, loc)
            return

        if path == "/creditbook/login":
            if web_user:
                _redirect(self, "/creditbook/dashboard")
                return
            flash_key = (qs.get("flash") or [None])[0]
            page = render_login_page(None, favicon_href, brand_img_src, flash_key=flash_key)
            _send_html_page(self, 200, page)
            return

        if path == "/creditbook/register":
            if web_user:
                _redirect(self, "/creditbook/dashboard")
                return
            page = render_register_page(None, favicon_href, brand_img_src, csrf_token_public("register"))
            _send_html_page(self, 200, page)
            return

        if path == "/creditbook/search_customers":
            if not web_user:
                self.send_response(401)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"error":"login"}')
                return
            q_raw = (qs.get("q") or [None])[0]
            search_q = unquote(q_raw).strip() if q_raw else None
            scope_raw = (qs.get("scope") or ["all"])[0]
            if scope_raw not in ("all", "cust", "txn"):
                scope_raw = "all"
            frag = render_dashboard_customer_rows_html(web_user.id, search_q, scope_raw)
            payload = json.dumps({"html": frag}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/creditbook/dashboard":
            if not web_user:
                _redirect(self, "/creditbook/login")
                return
            q_raw = (qs.get("q") or [None])[0]
            search_q = unquote(q_raw).strip() if q_raw else None
            scope_raw = (qs.get("scope") or ["all"])[0]
            if scope_raw not in ("all", "cust", "txn"):
                scope_raw = "all"
            flash_key = (qs.get("flash") or [None])[0]
            err_msg = (qs.get("err") or [None])[0]
            if err_msg:
                err_msg = unquote(err_msg)
            page = render_dashboard_html(
                web_user,
                favicon_href,
                brand_img_src,
                flash_key=flash_key,
                err_msg=err_msg,
                search_q=search_q,
                search_scope=scope_raw,
            )
            _send_html_page(self, 200, page)
            return

        if path == "/creditbook/feedback":
            if not web_user:
                _redirect(self, "/creditbook/login")
                return
            flash_key = (qs.get("flash") or [None])[0]
            err_msg = (qs.get("err") or [None])[0]
            if err_msg:
                err_msg = unquote(err_msg)
            page = render_feedback_page(
                web_user,
                favicon_href,
                brand_img_src,
                flash_key=flash_key,
                err_msg=err_msg,
            )
            _send_html_page(self, 200, page)
            return

        if path == "/creditbook/report":
            if not web_user:
                _redirect(self, "/creditbook/login")
                return
            o_raw = (qs.get("offset") or ["0"])[0]
            try:
                offset = max(0, int(o_raw))
            except ValueError:
                offset = 0
            time_order = (qs.get("time") or ["all"])[0].lower()
            if time_order not in ("new", "old", "all"):
                time_order = "all"
            amount_filter = (qs.get("amt") or ["all"])[0].lower()
            if amount_filter not in ("all", "high", "low"):
                amount_filter = "all"
            date_raw = (qs.get("date") or [None])[0]
            on_date = unquote(date_raw).strip()[:10] if date_raw else ""
            sq_raw = (qs.get("sq") or [None])[0]
            search_sq = unquote(sq_raw).strip() if sq_raw else ""
            rows, has_more = load_all_transactions_page(
                web_user.id,
                offset,
                REPORT_PAGE_SIZE,
                time_order=time_order,
                amount_filter=amount_filter,
                on_date=on_date or None,
                search_q=search_sq or None,
            )
            page = render_report_all_transactions_page(
                web_user,
                rows,
                offset,
                has_more,
                favicon_href,
                brand_img_src,
                time_order=time_order,
                amount_filter=amount_filter,
                on_date=on_date,
                search_sq=search_sq,
            )
            _send_html_page(self, 200, page)
            return

        if path == "/creditbook/account/tx_history_search":
            if not web_user:
                self.send_response(401)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"error":"login"}')
                return
            q_raw = (qs.get("q") or [None])[0]
            search_q = unquote(q_raw).strip() if q_raw else None
            frag = render_tx_history_rows_html(
                web_user.id, search_q, csrf_token(web_user.id, "tx_history")
            )
            payload = json.dumps({"html": frag}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/creditbook/account":
            if not web_user:
                _redirect(self, "/creditbook/login")
                return
            flash_key = (qs.get("flash") or [None])[0]
            err_msg = (qs.get("err") or [None])[0]
            if err_msg:
                err_msg = unquote(err_msg)
            db = SessionLocal()
            try:
                fresh = db.query(User).filter(User.id == web_user.id).first()
            finally:
                db.close()
            if not fresh:
                _redirect(self, "/creditbook/login")
                return
            page = render_account_page(
                fresh,
                favicon_href,
                brand_img_src,
                flash_key=flash_key,
                err_msg=err_msg,
            )
            _send_html_page(self, 200, page)
            return

        if path == "/creditbook/logout_confirm":
            if not web_user:
                _redirect(self, "/creditbook/login")
                return
            page = render_logout_confirm_page(web_user, favicon_href, brand_img_src)
            _send_html_page(self, 200, page)
            return

        tx_get = re.match(r"^/creditbook/tx/(?P<tid>\d+)$", path)
        if tx_get:
            if not web_user:
                _redirect(self, "/creditbook/login")
                return
            tid = int(tx_get.group("tid"))
            flash_key = (qs.get("flash") or [None])[0]
            err_msg = (qs.get("err") or [None])[0]
            if err_msg:
                err_msg = unquote(err_msg)
            page = render_tx_edit_page(
                web_user,
                tid,
                web_user.id,
                favicon_href,
                brand_img_src,
                flash_key=flash_key,
                err_msg=err_msg,
            )
            if page is None:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Not found")
                return
            _send_html_page(self, 200, page)
            return

        cust_share_m = re.match(r"^/creditbook/customer/(?P<cid>\d+)/share$", path)
        if cust_share_m:
            if not web_user:
                _redirect(self, "/creditbook/login")
                return
            cid = int(cust_share_m.group("cid"))
            view_url, wa_url, using_web, share_preview, err = build_customer_share_urls(web_user.id, cid)
            if err or not view_url or not wa_url:
                _redirect(
                    self,
                    f"/creditbook/customer/{cid}?err=" + quote(err or "تعذر إنشاء المشاركة.", safe=""),
                )
                return
            page = render_customer_share_page(
                web_user,
                cid,
                view_url,
                wa_url,
                using_web,
                share_preview or "",
                favicon_href,
                brand_img_src,
            )
            _send_html_page(self, 200, page)
            return

        cust_tx_search_m = re.match(r"^/creditbook/customer/(?P<cid>\d+)/tx_search$", path)
        if cust_tx_search_m:
            if not web_user:
                self.send_response(401)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"error":"login"}')
                return
            cid = int(cust_tx_search_m.group("cid"))
            q_raw = (qs.get("q") or [None])[0]
            search_q = unquote(q_raw).strip() if q_raw else None
            o_raw = (qs.get("offset") or ["0"])[0]
            try:
                tx_off = max(0, int(o_raw))
            except ValueError:
                tx_off = 0
            frag = render_customer_tx_list_fragment(web_user.id, cid, search_q, tx_off)
            if frag is None:
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"error":"not_found"}')
                return
            payload = json.dumps({"html": frag}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(payload)
            return

        cust_m = re.match(r"^/creditbook/customer/(?P<cid>\d+)$", path)
        if cust_m:
            if not web_user:
                _redirect(self, "/creditbook/login")
                return
            cid = int(cust_m.group("cid"))
            flash_key = (qs.get("flash") or [None])[0]
            err_msg = (qs.get("err") or [None])[0]
            if err_msg:
                err_msg = unquote(err_msg)
            q_raw = (qs.get("q") or [None])[0]
            search_q = unquote(q_raw).strip() if q_raw else None
            page = render_owner_customer_page(
                web_user,
                cid,
                web_user.id,
                offset,
                favicon_href,
                brand_img_src,
                flash_key=flash_key,
                err_msg=err_msg,
                search_q=search_q,
            )
            if page is None:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Not found")
                return
            _send_html_page(self, 200, page)
            return

        if path in ("/creditbook/logo", "/creditbook/logo.png"):
            try:
                data, ctype = _get_brand_logo_bytes_ctype()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Logo not found")
            return

        m = re.match(r"^/creditbook/balance/(?P<token>[A-Za-z0-9_-]+)$", path)
        photo_match = re.match(r"^/creditbook/photo/(?P<fid>.+)$", path)
        photo_view_match = re.match(r"^/creditbook/photo-view/(?P<fid>.+)$", path)
        if photo_match:
            file_id = unquote(photo_match.group("fid"))
            local = _try_local_web_photo(file_id)
            if local:
                data, ctype = local
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "private, max-age=3600")
                self.end_headers()
                self.wfile.write(data)
                return
            file_url = _resolve_telegram_file_url(file_id)
            if not file_url:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("الصورة غير متاحة".encode("utf-8"))
                return
            self.send_response(302)
            self.send_header("Location", file_url)
            self.end_headers()
            return

        if photo_view_match:
            file_id = unquote(photo_view_match.group("fid"))
            local = _try_local_web_photo(file_id)
            if local:
                enc = quote(file_id, safe="")
                self.send_response(302)
                self.send_header("Location", f"/creditbook/photo/{enc}")
                self.end_headers()
                return
            file_url = _resolve_telegram_file_url(file_id)
            if not file_url:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("الصورة غير متاحة".encode("utf-8"))
                return
            html = f"""
            <!doctype html>
            <html lang='ar' dir='rtl'>
              <head>
                <meta charset='utf-8'/>
                <meta name='viewport' content='width=device-width, initial-scale=1'/>
                <title>صورة المعاملة</title>
                <style>
                  body {{ margin: 0; background: #111; display: flex; justify-content: center; align-items: center; min-height: 100vh; }}
                  img {{ max-width: 96vw; max-height: 96vh; border-radius: 8px; }}
                </style>
              </head>
              <body>
                <img src="{file_url}" alt="صورة المعاملة"/>
              </body>
            </html>
            """
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
            return

        if not m:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        token = m.group("token")
        html = _render_page(token, offset)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        ct = self.headers.get("Content-Type") or ""
        mp: dict[str, str | tuple[bytes, str]] | None = None
        if "multipart/form-data" in ct.lower() and length > 0:
            try:
                mp = _parse_multipart_post(raw, ct)
            except Exception:
                mp = {}
        if mp is None:
            try:
                body = raw.decode("utf-8")
            except Exception:
                body = ""
            body_qs = parse_qs(body, keep_blank_values=True)
        else:
            body_qs = {}

        def _s(name: str) -> str:
            if mp is not None:
                v = mp.get(name, "")
                return v if isinstance(v, str) else ""
            return (body_qs.get(name) or [""])[0]

        def _f(name: str) -> tuple[bytes | None, str | None]:
            if mp is None:
                return None, None
            v = mp.get(name)
            if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], bytes):
                return v[0], v[1]
            return None, None

        secure = _request_is_secure(self)
        brand_img_src, favicon_href = _brand_visual_for_page()
        cookie_header = self.headers.get("Cookie")
        web_user = get_user_from_cookie_header(cookie_header)

        if path == "/creditbook/login":
            phone = _s("phone")
            pwd = _s("password")
            err, uid = try_login(phone, pwd)
            if err:
                page = render_login_page(err, favicon_href, brand_img_src, flash_key=None)
                _send_html_page(self, 200, page)
                return
            extra = _set_cookie_headers(uid, secure)
            _redirect(self, "/creditbook/dashboard", extra)
            return

        if path == "/creditbook/register":
            if web_user:
                _redirect(self, "/creditbook/dashboard")
                return
            csrf = _s("csrf")
            if not csrf_verify_public("register", csrf):
                page = render_register_page(
                    "انتهت صلاحية النموذج. حدّث الصفحة.",
                    favicon_href,
                    brand_img_src,
                    csrf_token_public("register"),
                )
                _send_html_page(self, 200, page)
                return
            err = action_register_web(
                _s("full_name"),
                _s("phone"),
                _s("password"),
                _s("password2"),
            )
            if err:
                page = render_register_page(err, favicon_href, brand_img_src, csrf_token_public("register"))
                _send_html_page(self, 200, page)
                return
            _redirect(self, "/creditbook/login?flash=reg_ok")
            return

        if path == "/creditbook/logout":
            extra = _clear_cookie_headers(secure)
            _redirect(self, "/creditbook/login", extra)
            return

        if not web_user:
            _redirect(self, "/creditbook/login")
            return

        uid = web_user.id

        def _e(msg: str) -> str:
            return quote(msg[:400], safe="")

        if path == "/creditbook/feedback":
            csrf = _s("csrf")
            if not csrf_verify(uid, "feedback_web", csrf):
                _redirect(self, "/creditbook/feedback?err=" + _e("انتهت صلاحية النموذج. حدّث الصفحة."))
                return
            kind = (_s("kind") or "problem").lower()
            if kind not in ("problem", "suggestion"):
                kind = "problem"
            msg = (_s("message") or "").strip()
            if len(msg) < 3:
                _redirect(self, "/creditbook/feedback?err=" + _e("النص قصير جداً."))
                return
            kind_ar = "مشكلة" if kind == "problem" else "اقتراح"
            body_txt = (
                f"📩 من الويب — {kind_ar}\n"
                f"المستخدم: {owner_display_name_for_user(web_user)} (id={web_user.id})\n"
                f"الهاتف: {(web_user.phone or '—').strip()}\n\n"
                f"{msg}"
            )
            if _send_telegram_admin_message(body_txt):
                _redirect(self, "/creditbook/feedback?flash=fb_ok")
            else:
                _redirect(
                    self,
                    "/creditbook/feedback?err=" + _e("تعذر الإرسال. حاول لاحقاً أو تواصل عبر «تواصل مع الدعم»."),
                )
            return

        if path == "/creditbook/account/profile":
            csrf = _s("csrf")
            if not csrf_verify(uid, "acct_profile", csrf):
                _redirect(self, "/creditbook/account?err=" + _e("انتهت صلاحية النموذج. حدّث الصفحة."))
                return
            err = action_user_update_profile(uid, _s("full_name"), _s("phone"))
            if err:
                _redirect(self, "/creditbook/account?err=" + _e(err))
                return
            _redirect(self, "/creditbook/account?flash=acc_prof")
            return

        if path == "/creditbook/account/password":
            csrf = _s("csrf")
            if not csrf_verify(uid, "acct_pass", csrf):
                _redirect(self, "/creditbook/account?err=" + _e("انتهت صلاحية النموذج. حدّث الصفحة."))
                return
            err = action_user_change_password(
                uid,
                _s("current_password"),
                _s("new_password"),
                _s("new_password2"),
            )
            if err:
                _redirect(self, "/creditbook/account?err=" + _e(err))
                return
            _redirect(self, "/creditbook/account?flash=acc_pwd")
            return

        if path == "/creditbook/account/tx_history_action":
            csrf = _s("csrf")
            if not csrf_verify(uid, "tx_history", csrf):
                _redirect(self, "/creditbook/account?err=" + _e("انتهت صلاحية النموذج. حدّث الصفحة."))
                return
            do = (_s("do") or "").strip().lower()
            try:
                hid = int((_s("hid") or "0").strip())
            except ValueError:
                hid = 0
            if not hid:
                _redirect(self, "/creditbook/account?err=" + _e("طلب غير صالح."))
                return
            if do == "restore":
                err = action_tx_history_restore(uid, hid)
                if err:
                    _redirect(self, "/creditbook/account?err=" + _e(err))
                    return
                _redirect(self, "/creditbook/account?flash=tx_hist_restore")
                return
            if do == "dismiss":
                err = action_tx_history_dismiss(uid, hid)
                if err:
                    _redirect(self, "/creditbook/account?err=" + _e(err))
                    return
                _redirect(self, "/creditbook/account?flash=tx_hist_dismiss")
                return
            _redirect(self, "/creditbook/account?err=" + _e("إجراء غير معروف."))
            return

        if path == "/creditbook/customer/create":
            csrf = _s("csrf")
            if not csrf_verify(uid, "cust_create", csrf):
                _redirect(self, "/creditbook/dashboard?err=" + _e("انتهت صلاحية النموذج. حدّث الصفحة."))
                return
            name = _s("name")
            phone = _s("phone")
            err, cid = action_customer_create(uid, name, phone)
            if err:
                _redirect(self, "/creditbook/dashboard?err=" + _e(err))
                return
            _redirect(self, f"/creditbook/customer/{cid}?flash=cust_new")
            return

        m_up = re.match(r"^/creditbook/customer/(?P<cid>\d+)/update$", path)
        if m_up:
            cid = int(m_up.group("cid"))
            csrf = _s("csrf")
            if not csrf_verify(uid, f"cust_upd_{cid}", csrf):
                _redirect(self, f"/creditbook/customer/{cid}?err=" + _e("انتهت صلاحية النموذج."))
                return
            err = action_customer_update(
                uid,
                cid,
                _s("name"),
                _s("phone"),
            )
            if err:
                _redirect(self, f"/creditbook/customer/{cid}?err=" + _e(err))
                return
            _redirect(self, f"/creditbook/customer/{cid}?flash=cust_upd")
            return

        m_del = re.match(r"^/creditbook/customer/(?P<cid>\d+)/delete$", path)
        if m_del:
            cid = int(m_del.group("cid"))
            csrf = _s("csrf")
            if not csrf_verify(uid, f"cust_del_{cid}", csrf):
                _redirect(self, f"/creditbook/customer/{cid}?err=" + _e("انتهت صلاحية النموذج."))
                return
            err = action_customer_delete(uid, cid)
            if err:
                _redirect(self, f"/creditbook/customer/{cid}?err=" + _e(err))
                return
            _redirect(self, "/creditbook/dashboard?flash=cust_del")
            return

        m_txn = re.match(r"^/creditbook/customer/(?P<cid>\d+)/txn_add$", path)
        if m_txn:
            cid = int(m_txn.group("cid"))
            csrf = _s("csrf")
            if not csrf_verify(uid, f"cust_txn_{cid}", csrf):
                _redirect(self, f"/creditbook/customer/{cid}?err=" + _e("انتهت صلاحية النموذج."))
                return
            kind = _s("kind").strip()
            pbytes, pname = _f("photo")
            err = action_txn_add(
                uid,
                cid,
                kind,
                _s("amount"),
                _s("note"),
                parse_tx_datetime(_s("txn_datetime")),
                pbytes,
                pname,
            )
            if err:
                _redirect(self, f"/creditbook/customer/{cid}?err=" + _e(err))
                return
            _redirect(self, f"/creditbook/customer/{cid}?flash=txn_ok")
            return

        m_tu = re.match(r"^/creditbook/tx/(?P<tid>\d+)/update$", path)
        if m_tu:
            tid = int(m_tu.group("tid"))
            csrf = _s("csrf")
            if not csrf_verify(uid, f"tx_edit_{tid}", csrf):
                _redirect(self, f"/creditbook/tx/{tid}?err=" + _e("انتهت صلاحية النموذج."))
                return
            pbytes, pname = _f("photo")
            err = action_tx_update(
                uid,
                tid,
                _s("amount"),
                _s("note"),
                parse_tx_datetime(_s("txn_datetime")),
                pbytes,
                pname,
                _s("remove_photo") == "1",
            )
            if err:
                _redirect(self, f"/creditbook/tx/{tid}?err=" + _e(err))
                return
            _redirect(self, f"/creditbook/tx/{tid}?flash=tx_upd")
            return

        m_tk = re.match(r"^/creditbook/tx/(?P<tid>\d+)/toggle_kind$", path)
        if m_tk:
            tid = int(m_tk.group("tid"))
            csrf = _s("csrf")
            if not csrf_verify(uid, f"tx_kind_{tid}", csrf):
                _redirect(self, f"/creditbook/tx/{tid}?err=" + _e("انتهت صلاحية النموذج."))
                return
            err = action_tx_toggle_kind(uid, tid)
            if err:
                _redirect(self, f"/creditbook/tx/{tid}?err=" + _e(err))
                return
            _redirect(self, f"/creditbook/tx/{tid}?flash=tx_kind")
            return

        m_td = re.match(r"^/creditbook/tx/(?P<tid>\d+)/delete$", path)
        if m_td:
            tid = int(m_td.group("tid"))
            csrf = _s("csrf")
            if not csrf_verify(uid, f"tx_del_{tid}", csrf):
                _redirect(self, f"/creditbook/tx/{tid}?err=" + _e("انتهت صلاحية النموذج."))
                return
            err, cid = action_tx_delete(uid, tid)
            if err:
                _redirect(self, f"/creditbook/tx/{tid}?err=" + _e(err))
                return
            if cid:
                _redirect(self, f"/creditbook/customer/{cid}?flash=tx_del")
            else:
                _redirect(self, "/creditbook/dashboard?flash=tx_del")
            return

        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Not found")


def start_web_server(port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    return httpd

