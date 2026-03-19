# -*- coding: utf-8 -*-
"""
موقع بسيط يعرض معلومات العميل عند فتح رابط المشاركة.
يعمل داخل نفس كونتainer البوت (Thread) بدون dependencies إضافية.
"""

from __future__ import annotations

import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from database import SessionLocal
from app_models import ShareLink, CustomerTransaction, Customer


TX_PAGE_SIZE = 15


def _amount_to_str(x) -> str:
    try:
        return f"{float(x):.2f}"
    except Exception:
        return str(x)


def _kind_icon(kind: str) -> str:
    return "🔴" if kind == "took" else "🟢"


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

        has_more = offset + TX_PAGE_SIZE < total
        more_offset = offset + TX_PAGE_SIZE

        tx_rows = []
        for t in txs:
            dt = t.created_at.strftime("%Y-%m-%d %H:%M")
            note = (t.note or "").strip()
            note_html = f"<div class='note'>ملاحظة: {note}</div>" if note else ""
            tx_rows.append(
                f"""
                <div class='tx'>
                  <div class='top'>{dt}</div>
                  <div class='main'>{_kind_icon(t.kind)} {_kind_label(t.kind)} - {_amount_to_str(t.amount)} د.ع.</div>
                  {note_html}
                </div>
                """
            )

        more_btn = ""
        if has_more:
            # offset button keep same token
            more_btn = f"<a class='btn' href='/creditbook/balance/{token}?lang=ar&offset={more_offset}'>➕ عرض الباقيات</a>"

        balance_text = "الرصيد: "
        if bal > 0:
            balance_text += f"{bal:.2f} (مدين لك)"
        elif bal < 0:
            balance_text += f"{abs(bal):.2f} (أنت مدين)"
        else:
            balance_text += "0"

        title = f"عميل: {cust.name}"
        return f"""
        <!doctype html>
        <html lang='ar' dir='rtl'>
          <head>
            <meta charset='utf-8'/>
            <meta name='viewport' content='width=device-width, initial-scale=1'/>
            <title>{title}</title>
            <style>
              body {{ font-family: Arial, sans-serif; background: #f7f7f7; padding: 16px; }}
              .card {{ background: #fff; border-radius: 12px; padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
              h2 {{ margin-top: 0; }}
              .balance {{ font-size: 18px; margin: 8px 0 16px 0; }}
              .tx {{ background: #fafafa; border: 1px solid #eee; border-radius: 10px; padding: 12px; margin: 10px 0; }}
              .top {{ color: #666; font-size: 12px; }}
              .main {{ margin-top: 6px; font-size: 15px; }}
              .note {{ margin-top: 6px; color: #444; font-size: 13px; }}
              .btn {{ display: inline-block; padding: 10px 14px; background: #1976d2; color: #fff; text-decoration: none; border-radius: 10px; margin-top: 10px; }}
            </style>
          </head>
          <body>
            <div class='card'>
              <h2>{cust.name}</h2>
              <div class='balance'>{balance_text}</div>
              {''.join(tx_rows) if tx_rows else '<p>لا توجد معاملات.</p>'}
              {more_btn}
            </div>
          </body>
        </html>
        """
    finally:
        db.close()


def _kind_label(kind: str) -> str:
    # kind is "took" => أخذت (red), "gave" => أعطيت (green)
    return "أخذت" if kind == "took" else "أعطيت"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        offset = 0
        if "offset" in qs:
            try:
                offset = int(qs["offset"][0])
            except Exception:
                offset = 0

        m = re.match(r"^/creditbook/balance/(?P<token>[A-Za-z0-9_-]+)$", path)
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
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


def start_web_server(port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    return httpd

