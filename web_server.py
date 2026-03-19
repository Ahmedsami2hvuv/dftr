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
from utils.phone import wa_number as _wa_number

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
            remain_class = "bal-red" if remain > 0 else ("bal-green" if remain < 0 else "")
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
        balance_class = "bal-red" if bal > 0 else ("bal-green" if bal < 0 else "")

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
        owner_meta = (
            f"<span class='k'>صاحب الحساب:</span> {_html_escape(owner_name)}"
            + (f" — {_html_escape(owner_phone)}" if owner_phone else "")
        )
        phone_disp = _html_escape((cust.phone or "").strip())
        cust_meta = (
            f"<span class='k'>العميل:</span> {_html_escape(cust.name)}"
            + (f" — {phone_disp}" if phone_disp else "")
        )
        return f"""
        <!doctype html>
        <html lang='ar' dir='rtl'>
          <head>
            <meta charset='utf-8'/>
            <meta name='viewport' content='width=device-width, initial-scale=1'/>
            <link rel='icon' href='{_html_escape(favicon_href)}' type='image/png'/>
            <title>{title}</title>
            <style>
              body {{ font-family: Arial, sans-serif; background: #f7f7f7; padding: 16px; }}
              .card {{ background: #fff; border-radius: 12px; padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
              .brand {{
                display: flex;
                flex-direction: row;
                align-items: center;
                justify-content: flex-start;
                gap: 12px;
                flex-wrap: nowrap;
                margin-bottom: 14px;
              }}
              .brand h2 {{
                margin: 0;
                font-size: 1.85rem;
                font-weight: 800;
                color: #0f172a;
                line-height: 1.15;
                letter-spacing: -0.02em;
              }}
              .brand-logo {{
                width: 56px;
                height: 56px;
                border-radius: 14px;
                object-fit: cover;
                flex-shrink: 0;
                box-shadow: 0 3px 10px rgba(0,0,0,.12);
              }}
              .balance {{ font-size: 18px; margin: 8px 0 16px 0; }}
              .bal-red {{ color: #d32f2f; }}
              .bal-green {{ color: #2e7d32; }}
              .tx {{ background: #fafafa; border: 1px solid #eee; border-radius: 10px; padding: 12px; margin: 10px 0; }}
              .top {{ color: #666; font-size: 12px; }}
              .tx-content {{ margin-top: 6px; display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }}
              .tx-text {{ flex: 1; min-width: 0; }}
              .main {{ margin-top: 6px; font-size: 15px; font-weight: 700; }}
              .remain {{ margin-top: 6px; font-size: 13px; }}
              .remain.bal-red {{ color: #d32f2f; }}
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
              .wa {{ background: linear-gradient(135deg, #22c55e, #15803d); margin-left: 8px; }}
              .bot {{ background: linear-gradient(135deg, #3b82f6, #1d4ed8); }}
              .meta {{ margin-bottom: 6px; line-height: 1.45; }}
              .meta .k {{ color: #64748b; font-weight: 600; margin-inline-end: 6px; }}
              .owner-line {{ font-size: 14px; color: #475569; }}
              .customer-line {{ font-size: 13px; color: #64748b; font-weight: 500; }}
              .actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }}
            </style>
          </head>
          <body>
            <div class='card'>
              <div class='brand'>
                <img class='brand-logo' src="{_html_escape(brand_img_src)}" width='56' height='56' alt='دفتر الديون'/>
                <h2>دفتر الديون</h2>
              </div>
              <div class='meta owner-line'>{owner_meta}</div>
              <div class='meta customer-line'>{cust_meta}</div>
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
                self.send_header("Cache-Control", "public, max-age=86400")
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

