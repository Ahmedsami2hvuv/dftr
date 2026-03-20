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
from urllib.request import urlopen

from database import SessionLocal
from app_models import BRAND_LOGO_SETTING_KEY, Customer, CustomerTransaction, ShareLink, SiteSetting
from config import BOT_LOGO_BASE64
from config import BOT_TOKEN
from config import BOT_USERNAME
from utils.phone import format_phone_iq_local_display, wa_number as _wa_number

# شعار البوت (احتياطي إذا لم يُضبط BOT_LOGO_BASE64 في Railway)
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_LOGO_PATH = _STATIC_DIR / "bot_logo.png"
_LOGO_B64_PATH = _STATIC_DIR / "bot_logo.b64.txt"

TX_PAGE_SIZE = 15


def _html_escape(s: str) -> str:
    return html.escape(s or "", quote=True)


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
            # موجب = لصالحك (أخضر) كما في البوت
            remain_class = "bal-green" if remain > 0 else ("bal-red" if remain < 0 else "")
            tx_kind_class = "bal-red" if t.kind == "took" else "bal-green"
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
        balance_class = "bal-green" if bal > 0 else ("bal-red" if bal < 0 else "")

        owner = cust.user
        owner_name = (owner.full_name or owner.username or "صاحب الحساب") if owner else "صاحب الحساب"
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
        # بدون تسميات «صاحب الحساب / العميل» — الاسم والرقم فقط، رقم محلي 11 رقم يبدأ بـ 0
        owner_disp_phone = format_phone_iq_local_display(owner_phone) if owner_phone else ""
        owner_name_esc = _html_escape(owner_name)
        if owner_disp_phone and owner_phone:
            wa_phone_card = _wa_number(owner_phone)
            wa_href_card = _html_escape(f"https://api.whatsapp.com/send?phone={wa_phone_card}")
            owner_phone_html = (
                f"<a class='owner-phone owner-phone-wa' href='{wa_href_card}' "
                f"target='_blank' rel='noopener' dir='ltr' title='فتح واتساب'>{_html_escape(owner_disp_phone)}</a>"
            )
        else:
            owner_phone_html = "<span class='owner-phone-muted'>لم يُضف رقم بعد</span>"
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
                font-family: 'Tajawal', Arial, sans-serif;
                background: linear-gradient(160deg, #ecfdf5 0%, #f0fdfa 35%, #f7f7f7 100%);
                padding: 16px;
                margin: 0;
              }}
              .card {{
                background: #fff;
                border-radius: 20px;
                padding: 20px;
                box-shadow: 0 8px 32px rgba(15, 118, 110, 0.1), 0 1px 4px rgba(0,0,0,0.06);
                border: 1px solid rgba(13, 148, 136, 0.12);
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
                color: #0f172a;
                line-height: 1.2;
                letter-spacing: -0.03em;
                background: linear-gradient(120deg, #0f766e, #0d9488, #14b8a6);
                -webkit-background-clip: text;
                background-clip: text;
                -webkit-text-fill-color: transparent;
              }}
              .brand-logo {{
                width: 64px;
                height: 64px;
                border-radius: 18px;
                object-fit: cover;
                flex-shrink: 0;
                box-shadow: 0 8px 24px rgba(13, 148, 136, 0.35);
                border: 3px solid rgba(255,255,255,0.95);
              }}
              /* افتراضي = موبايل: صف واحد للاسم + الرقم، صندوق مضغوط */
              .owner-showcase {{
                flex: 0 1 auto;
                max-width: min(100%, 92vw);
                min-width: 0;
                background: linear-gradient(145deg, #0d9488 0%, #0f766e 55%, #115e59 100%);
                color: #fff;
                padding: 8px 10px;
                border-radius: 12px;
                box-shadow:
                  0 6px 18px rgba(15, 118, 110, 0.35),
                  inset 0 1px 0 rgba(255,255,255,0.15);
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
                font-size: 0.62rem;
                font-weight: 800;
                letter-spacing: 0.06em;
                opacity: 0.9;
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
                font-size: clamp(0.92rem, 3.6vw, 1.12rem);
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
              .bal-red {{ color: #c62828; }}
              .bal-green {{ color: #2e7d32; }}
              .tx {{ background: #fafafa; border: 1px solid #eee; border-radius: 10px; padding: 12px; margin: 10px 0; }}
              .top {{ color: #666; font-size: 12px; }}
              .tx-content {{ margin-top: 6px; display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }}
              .tx-text {{ flex: 1; min-width: 0; }}
              .main {{ margin-top: 6px; font-size: 15px; font-weight: 700; }}
              .remain {{ margin-top: 6px; font-size: 13px; }}
              .remain.bal-red {{ color: #c62828; }}
              .remain.bal-green {{ color: #2e7d32; }}
              .note {{ margin-top: 6px; color: #444; font-size: 13px; }}
              .photo-wrap {{ flex: 0 0 auto; margin-top: 2px; }}
              .photo {{ width: 56px; height: 56px; object-fit: cover; border-radius: 8px; border: 1px solid #ddd; cursor: pointer; }}
              .btn {{
                display: inline-block;
                padding: 10px 16px;
                color: #fff;
                text-decoration: none;
                border-radius: 999px;
                margin-top: 10px;
                font-weight: 700;
                font-size: 14px;
                box-shadow: 0 6px 14px rgba(0,0,0,.14);
                transition: transform .15s ease, box-shadow .15s ease, opacity .15s ease;
              }}
              .btn:hover {{
                transform: translateY(-1px);
                box-shadow: 0 9px 18px rgba(0,0,0,.18);
                opacity: .95;
              }}
              .wa {{ background: linear-gradient(135deg, #22c55e, #15803d); margin-inline-start: 8px; }}
              .bot {{ background: linear-gradient(135deg, #3b82f6, #1d4ed8); }}
              .meta {{ margin-bottom: 10px; line-height: 1.5; }}
              .customer-line {{ font-size: 15px; color: #334155; font-weight: 600; }}
              .actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }}
              /* حاسوب: بطاقة أكبر، الاسم فوق والرقم تحت */
              @media (min-width: 561px) {{
                .owner-showcase {{
                  max-width: min(420px, 42vw);
                  padding: 16px 20px;
                  border-radius: 16px;
                }}
                .owner-badge {{
                  font-size: 0.72rem;
                  margin-bottom: 10px;
                }}
                .owner-name-row {{
                  flex-direction: column;
                  align-items: stretch;
                  gap: 14px;
                  flex-wrap: nowrap;
                }}
                .owner-name {{
                  font-size: clamp(1.22rem, 1.85vw, 1.52rem);
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
                    <div class='owner-name'>{owner_name_esc}</div>
                    {owner_phone_html}
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


def start_web_server(port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    return httpd

