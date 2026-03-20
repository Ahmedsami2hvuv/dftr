# -*- coding: utf-8 -*-
"""
واجهة ويب لدفتر الديون: تسجيل دخول بالهاتف وكلمة المرور، عرض العملاء والمعاملات.
نفس أسلوب صفحة «مشاركة الرصيد» للعميل (Tajawal، بطاقة، ألوان).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import re
import time
from urllib.parse import quote

from database import SessionLocal
from app_models import Customer, CustomerTransaction, User
from config import BOT_USERNAME, web_session_secret
from utils.password import check_password
from utils.phone import format_phone_iq_local_display, normalize_phone, same_phone

SESSION_COOKIE = "dftr_web"
SESSION_DAYS = 30
TX_PAGE_SIZE = 15


def _html_escape(s: str) -> str:
    return html.escape(s or "", quote=True)


def _find_user_by_phone(db, phone_normalized: str) -> User | None:
    u = db.query(User).filter(User.phone == phone_normalized).first()
    if u:
        return u
    for u in db.query(User).filter(User.phone.isnot(None)):
        if same_phone(u.phone, phone_normalized):
            return u
    return None


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes | None:
    try:
        pad = "=" * (-len(s) % 4)
        return base64.urlsafe_b64decode(s + pad)
    except Exception:
        return None


def session_sign_user_id(user_id: int) -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{user_id}.{exp}".encode("utf-8")
    sig = hmac.new(web_session_secret().encode("utf-8"), payload, hashlib.sha256).digest()
    token = _b64url_encode(payload) + "." + _b64url_encode(sig)
    return token


def session_read_user_id(token: str | None) -> int | None:
    if not token or "." not in token:
        return None
    try:
        b64_pl, b64_sig = token.rsplit(".", 1)
        raw_pl = _b64url_decode(b64_pl)
        raw_sig = _b64url_decode(b64_sig)
        if not raw_pl or not raw_sig:
            return None
        parts = raw_pl.decode("utf-8").split(".")
        if len(parts) != 2:
            return None
        uid_s, exp_s = parts
        uid = int(uid_s)
        exp = int(exp_s)
        if exp < int(time.time()):
            return None
        expected = hmac.new(
            web_session_secret().encode("utf-8"),
            raw_pl,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, raw_sig):
            return None
        return uid
    except Exception:
        return None


def get_user_from_cookie_header(cookie_header: str | None) -> User | None:
    if not cookie_header:
        return None
    m = re.search(r"\b" + re.escape(SESSION_COOKIE) + r"=([^;]+)", cookie_header)
    if not m:
        return None
    token = m.group(1).strip()
    uid = session_read_user_id(token)
    if not uid:
        return None
    db = SessionLocal()
    try:
        return db.query(User).filter(User.id == uid).first()
    finally:
        db.close()


def _set_cookie_headers(user_id: int, secure: bool) -> list[tuple[str, str]]:
    token = session_sign_user_id(user_id)
    max_age = SESSION_DAYS * 86400
    flags = "HttpOnly; Path=/; SameSite=Lax; Max-Age=" + str(max_age)
    if secure:
        flags += "; Secure"
    val = f"{SESSION_COOKIE}={token}; {flags}"
    return [("Set-Cookie", val)]


def _clear_cookie_headers(secure: bool) -> list[tuple[str, str]]:
    flags = "HttpOnly; Path=/; SameSite=Lax; Max-Age=0"
    if secure:
        flags += "; Secure"
    return [("Set-Cookie", f"{SESSION_COOKIE}=; {flags}")]


def _amount_to_str(x) -> str:
    try:
        return f"{float(x):.2f}"
    except Exception:
        return str(x)


def render_login_page(error: str | None, favicon_href: str, brand_img: str) -> str:
    err = f"<div class='err'>{_html_escape(error)}</div>" if error else ""
    return f"""
    <!doctype html>
    <html lang='ar' dir='rtl'>
      <head>
        <meta charset='utf-8'/>
        <meta name='viewport' content='width=device-width, initial-scale=1'/>
        <link rel='icon' href='{_html_escape(favicon_href)}' type='image/png'/>
        <link rel='stylesheet' href='/creditbook/static/creditbook_app.css'/>
        <link rel='preconnect' href='https://fonts.googleapis.com'/>
        <link rel='preconnect' href='https://fonts.gstatic.com' crossorigin/>
        <link href='https://fonts.googleapis.com/css2?family=Tajawal:wght@400;600;700;800;900&display=swap' rel='stylesheet'/>
        <title>تسجيل الدخول — دفتر الديون</title>
      </head>
      <body>
        <div class='card'>
          <div class='brand-header'>
            <div class='brand'>
              <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt='دفتر الديون'/>
              <h2>دفتر الديون</h2>
            </div>
          </div>
          <p class='hint'>سجّل الدخول بنفس رقم الهاتف وكلمة المرور المستخدمين في البوت.</p>
          {err}
          <form class='login-form' method='post' action='/creditbook/login' autocomplete='on'>
            <label for='phone'>رقم الهاتف</label>
            <input type='tel' id='phone' name='phone' required placeholder='+9647xxxxxxxx' dir='ltr'/>
            <label for='password'>كلمة المرور</label>
            <input type='password' id='password' name='password' required autocomplete='current-password'/>
            <p style='margin-top:16px'>
              <button type='submit' class='btn btn-primary'>تسجيل الدخول</button>
            </p>
          </form>
          {f"<p style='margin-top:12px'><a class='btn btn-bot' href='https://t.me/{BOT_USERNAME}' target='_blank' rel='noopener'>فتح البوت في تيليجرام</a></p>" if BOT_USERNAME else ""}
        </div>
      </body>
    </html>
    """


def render_dashboard_html(user: User, customers: list[tuple[Customer, float]], favicon_href: str, brand_img: str) -> str:
    rows = []
    for c, bal in customers:
        phone_disp = format_phone_iq_local_display((c.phone or "").strip()) if c.phone else ""
        meta = _html_escape(c.name) + (f" — {_html_escape(phone_disp)}" if phone_disp else "")
        bc = "bal-red" if bal > 0 else ("bal-green" if bal < 0 else "")
        rows.append(
            f"""
            <a class='cust-row' href='/creditbook/customer/{c.id}'>
              <div class='cust-name'>{_html_escape(c.name)}</div>
              <div class='cust-meta'>{meta}</div>
              <div class='cust-bal {bc}'>الرصيد الحالي: {_amount_to_str(bal)} د.ع.</div>
            </a>
            """
        )
    body = "".join(rows) if rows else "<p>لا يوجد عملاء بعد. أضف عملاء من البوت.</p>"
    disp = _html_escape(user.full_name or user.username or "حسابي")
    return f"""
    <!doctype html>
    <html lang='ar' dir='rtl'>
      <head>
        <meta charset='utf-8'/>
        <meta name='viewport' content='width=device-width, initial-scale=1'/>
        <link rel='icon' href='{_html_escape(favicon_href)}' type='image/png'/>
        <link rel='stylesheet' href='/creditbook/static/creditbook_app.css'/>
        <link rel='preconnect' href='https://fonts.googleapis.com'/>
        <link rel='preconnect' href='https://fonts.gstatic.com' crossorigin/>
        <link href='https://fonts.googleapis.com/css2?family=Tajawal:wght@400;600;700;800;900&display=swap' rel='stylesheet'/>
        <title>دفتر الديون — عملائي</title>
      </head>
      <body>
        <div class='card'>
          <div class='brand-header'>
            <div class='brand'>
              <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt=''/>
              <h2>دفتر الديون</h2>
            </div>
          </div>
          <div class='toolbar'>
            <span style='font-weight:800;color:#0f172a'>👤 {disp}</span>
            <form method='post' action='/creditbook/logout' style='display:inline;margin:0'>
              <button type='submit' class='btn btn-secondary' style='margin-top:0'>تسجيل الخروج</button>
            </form>
            {f"<a class='btn btn-bot' style='margin-top:0' href='https://t.me/{BOT_USERNAME}' target='_blank' rel='noopener'>البوت</a>" if BOT_USERNAME else ""}
          </div>
          <p class='hint'>قائمة العملاء والرصيد (عرض فقط — الإضافة والتعديل من البوت).</p>
          {body}
        </div>
      </body>
    </html>
    """


def _owner_kind_label(kind: str) -> str:
    return "🟢 أعطيت" if kind == "gave" else "🔴 أخذت"


def _owner_kind_class(kind: str) -> str:
    return "bal-green" if kind == "gave" else "bal-red"


def render_owner_customer_page(
    user: User,
    customer_id: int,
    owner_user_id: int,
    offset: int,
    favicon_href: str,
    brand_img: str,
) -> str | None:
    """صفحة عميل من منظور صاحب الدفتر (مثل تقرير المشاركة لكن بأعطيت/أخذت للمالك)."""
    db = SessionLocal()
    try:
        cust = (
            db.query(Customer)
            .filter(Customer.id == customer_id, Customer.user_id == owner_user_id)
            .first()
        )
        if not cust:
            return None

        gave = sum(float(t.amount or 0) for t in cust.transactions if t.kind == "gave")
        took = sum(float(t.amount or 0) for t in cust.transactions if t.kind == "took")
        bal = gave - took

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
            note_html = f"<div class='note'>ملاحظة: {_html_escape(note)}</div>" if note else "<div class='note'>ملاحظة: —</div>"
            photo_html = ""
            if getattr(t, "photo_file_id", None):
                fid = quote(str(t.photo_file_id), safe="")
                photo_html = (
                    f"<div class='photo-wrap'><a href='/creditbook/photo-view/{fid}' target='_blank' rel='noopener'>"
                    f"<img class='photo' src='/creditbook/photo/{fid}' alt='صورة'/></a></div>"
                )
            remain = running_after_by_tx.get(t.id, bal)
            remain_class = "bal-red" if remain > 0 else ("bal-green" if remain < 0 else "")
            kc = _owner_kind_class(t.kind)
            tx_rows.append(
                f"""
                <div class='tx'>
                  <div class='top'>{dt}</div>
                  <div class='tx-content'>
                    <div class='tx-text'>
                      <div class='remain {remain_class}'>الرصيد بعد المعاملة: {_amount_to_str(remain)} د.ع.</div>
                      <div class='main {kc}'>{_owner_kind_label(t.kind)} — {_amount_to_str(t.amount)} د.ع.</div>
                      {note_html}
                    </div>
                    {photo_html}
                  </div>
                </div>
                """
            )

        more_btn = ""
        if has_more:
            more_btn = f"<a class='btn btn-primary' href='/creditbook/customer/{cust.id}?offset={more_offset}'>➕ عرض المزيد</a>"

        balance_text = "📌 الرصيد الحالي: "
        if bal > 0:
            balance_text += f"{bal:.2f}"
        elif bal < 0:
            balance_text += f"{abs(bal):.2f}"
        else:
            balance_text += "0"
        balance_class = "bal-red" if bal > 0 else ("bal-green" if bal < 0 else "")

        cust_disp_phone = format_phone_iq_local_display((cust.phone or "").strip()) if cust.phone else ""
        cust_meta = _html_escape(cust.name) + (f" — {_html_escape(cust_disp_phone)}" if cust_disp_phone else "")

        owner_disp = _html_escape(user.full_name or user.username or "حسابي")

        html_out = f"""
        <!doctype html>
        <html lang='ar' dir='rtl'>
          <head>
            <meta charset='utf-8'/>
            <meta name='viewport' content='width=device-width, initial-scale=1'/>
            <link rel='icon' href='{_html_escape(favicon_href)}' type='image/png'/>
            <link rel='stylesheet' href='/creditbook/static/creditbook_app.css'/>
            <link rel='preconnect' href='https://fonts.googleapis.com'/>
            <link rel='preconnect' href='https://fonts.gstatic.com' crossorigin/>
            <link href='https://fonts.googleapis.com/css2?family=Tajawal:wght@400;600;700;800;900&display=swap' rel='stylesheet'/>
            <title>{_html_escape(cust.name)} — دفتر الديون</title>
          </head>
          <body>
            <div class='card'>
              <div class='brand-header'>
                <div class='brand'>
                  <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt=''/>
                  <h2>دفتر الديون</h2>
                </div>
              </div>
              <div class='toolbar'>
                <a class='btn btn-secondary' href='/creditbook/dashboard'>◀ العملاء</a>
                <form method='post' action='/creditbook/logout' style='display:inline;margin:0'>
                  <button type='submit' class='btn btn-secondary' style='margin-top:0'>تسجيل الخروج</button>
                </form>
              </div>
              <p style='margin:6px 0 4px;font-weight:700;color:#475569'>👤 {owner_disp}</p>
              <div class='cust-meta' style='margin-bottom:8px;font-size:1.05rem;font-weight:700;color:#0f172a'>👤 {cust_meta}</div>
              <div class='cust-bal {balance_class}' style='font-size:1.25rem;margin-bottom:12px'>{balance_text} د.ع.</div>
              {''.join(tx_rows) if tx_rows else '<p>لا توجد معاملات.</p>'}
              {more_btn}
            </div>
          </body>
        </html>
        """
        return html_out
    finally:
        db.close()


def try_login(phone_raw: str, password: str) -> tuple[str | None, int | None]:
    """إرجاع (رسالة خطأ، None) أو (None, user_id) عند النجاح."""
    phone = normalize_phone(phone_raw or "")
    if not phone:
        return ("أدخل رقم هاتف صالحاً.", None)
    if not (password or "").strip():
        return ("أدخل كلمة المرور.", None)
    db = SessionLocal()
    try:
        user = _find_user_by_phone(db, phone)
        if not user:
            return ("رقم الهاتف أو كلمة المرور غير صحيحة.", None)
        if not user.password_hash:
            return ("هذا الحساب لا يملك كلمة مرور بعد. سجّل الدخول من البوت واضبط كلمة المرور.", None)
        if not check_password(password.strip(), user.password_hash):
            return ("رقم الهاتف أو كلمة المرور غير صحيحة.", None)
        return (None, user.id)
    finally:
        db.close()


def load_dashboard_rows(user_id: int) -> list[tuple[Customer, float]]:
    db = SessionLocal()
    try:
        customers = (
            db.query(Customer)
            .filter(Customer.user_id == user_id)
            .order_by(Customer.name.asc())
            .all()
        )
        out = []
        for c in customers:
            gave = sum(float(t.amount or 0) for t in c.transactions if t.kind == "gave")
            took = sum(float(t.amount or 0) for t in c.transactions if t.kind == "took")
            out.append((c, gave - took))
        return out
    finally:
        db.close()
