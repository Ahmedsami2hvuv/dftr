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


def csrf_token(uid: int, action: str) -> str:
    """رمز بسيط مرتبط بالجلسة والساعة لحماية النماذج."""
    tb = int(time.time()) // 3600
    msg = f"{uid}:{action}:{tb}".encode("utf-8")
    sig = hmac.new(web_session_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{tb}.{sig}"


def csrf_verify(uid: int, action: str, token: str) -> bool:
    if not token or "." not in token:
        return False
    parts = token.split(".", 1)
    if len(parts) != 2:
        return False
    tb_s, sig = parts
    try:
        tb = int(tb_s)
    except ValueError:
        return False
    now_b = int(time.time()) // 3600
    if tb not in (now_b, now_b - 1, now_b + 1):
        return False
    msg = f"{uid}:{action}:{tb}".encode("utf-8")
    expected = hmac.new(web_session_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


FLASH_LABELS = {
    "cust_new": "تمت إضافة العميل ✅",
    "cust_upd": "تم حفظ بيانات العميل ✅",
    "cust_del": "تم حذف العميل ✅",
    "txn_ok": "تمت إضافة المعاملة ✅",
    "tx_upd": "تم حفظ المعاملة ✅",
    "tx_kind": "تم تغيير نوع المعاملة ✅",
    "tx_del": "تم حذف المعاملة ✅",
}


def _flash_block(flash_key: str | None, err_msg: str | None) -> str:
    out = []
    if flash_key and flash_key in FLASH_LABELS:
        out.append(f"<div class='flash-ok'>{_html_escape(FLASH_LABELS[flash_key])}</div>")
    if err_msg:
        out.append(f"<div class='err'>{_html_escape(err_msg)}</div>")
    return "".join(out)


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


def render_dashboard_html(
    user: User,
    customers: list[tuple[Customer, float]],
    favicon_href: str,
    brand_img: str,
    flash_key: str | None = None,
    err_msg: str | None = None,
) -> str:
    uid = user.id
    csrf_c = csrf_token(uid, "cust_create")
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
    body = "".join(rows) if rows else "<p class='hint'>لا يوجد عملاء بعد — أضف عميلاً بالنموذج أدناه.</p>"
    disp = _html_escape(user.full_name or user.username or "حسابي")
    flash_html = _flash_block(flash_key, err_msg)
    add_form = f"""
    <div class='web-section'>
      <h3 class='web-h3'>➕ عميل جديد</h3>
      <form method='post' action='/creditbook/customer/create' class='stack-form'>
        <input type='hidden' name='csrf' value='{_html_escape(csrf_c)}'/>
        <label for='cname'>الاسم</label>
        <input type='text' id='cname' name='name' required maxlength='255' placeholder='اسم العميل'/>
        <label for='cphone'>الهاتف (اختياري)</label>
        <input type='tel' id='cphone' name='phone' placeholder='+9647… أو 077…' dir='ltr'/>
        <button type='submit' class='btn btn-primary'>حفظ العميل</button>
      </form>
    </div>
    """
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
          {flash_html}
          <p class='hint'>إدارة العملاء والمعاملات من المتصفح أو من البوت.</p>
          {add_form}
          <h3 class='web-h3' style='margin-top:8px'>📋 عملائي</h3>
          {body}
        </div>
      </body>
    </html>
    """


def _owner_kind_word(kind: str) -> str:
    """نص النوع بدون الرمز (الرمز يُعرض في سطر منفصل أعلى/أسفل حسب الحالة)."""
    return "أعطيت" if kind == "gave" else "أخذت"


def _owner_kind_class(kind: str) -> str:
    return "bal-green" if kind == "gave" else "bal-red"


def render_owner_customer_page(
    user: User,
    customer_id: int,
    owner_user_id: int,
    offset: int,
    favicon_href: str,
    brand_img: str,
    flash_key: str | None = None,
    err_msg: str | None = None,
) -> str | None:
    """صفحة عميل من منظور صاحب الدفتر + نماذج التعديل."""
    db = SessionLocal()
    try:
        cust = (
            db.query(Customer)
            .filter(Customer.id == customer_id, Customer.user_id == owner_user_id)
            .first()
        )
        if not cust:
            return None

        uid = owner_user_id
        csrf_u = csrf_token(uid, f"cust_upd_{customer_id}")
        csrf_d = csrf_token(uid, f"cust_del_{customer_id}")
        csrf_t = csrf_token(uid, f"cust_txn_{customer_id}")

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
            note_html = (
                f"<div class='tx-note-line'><span class='tx-note-label'>ملاحظة:</span> {_html_escape(note)}</div>"
                if note
                else ""
            )
            photo_html = ""
            if getattr(t, "photo_file_id", None):
                fid = quote(str(t.photo_file_id), safe="")
                photo_html = (
                    f"<a class='tx-inline-photo' href='/creditbook/photo-view/{fid}' target='_blank' rel='noopener' title='صورة'>"
                    f"<img class='photo' src='/creditbook/photo/{fid}' alt=''/></a>"
                )
            remain = running_after_by_tx.get(t.id, bal)
            remain_class = "bal-red" if remain > 0 else ("bal-green" if remain < 0 else "")
            kc = _owner_kind_class(t.kind)
            kind_word = _owner_kind_word(t.kind)
            # أعطيت: الدائرة الخضراء فوق السطر | أخذت: الدائرة الحمراء تحت السطر (فوق الملاحظة إن وُجدت)
            dot_top = "<div class='tx-emoji-row tx-emoji-above' aria-hidden='true'><span class='tx-emoji-dot'>🟢</span></div>" if t.kind == "gave" else ""
            dot_bottom = "<div class='tx-emoji-row tx-emoji-below' aria-hidden='true'><span class='tx-emoji-dot'>🔴</span></div>" if t.kind == "took" else ""
            tx_rows.append(
                f"""
                <div class='tx tx-row-{t.kind}'>
                  {dot_top}
                  <div class='tx-line-one'>
                    <span class='tx-date' dir='ltr'>{dt}</span>
                    <span class='tx-sep' aria-hidden='true'>·</span>
                    <a class='tx-edit-btn' href='/creditbook/tx/{t.id}'><span class='tx-edit-ico'>✎</span> تعديل</a>
                    <span class='tx-sep' aria-hidden='true'>·</span>
                    <span class='tx-remain {remain_class}'>بعدها {_amount_to_str(remain)} د.ع.</span>
                    <span class='tx-sep' aria-hidden='true'>·</span>
                    <span class='tx-kind-amt {kc}'><span class='tx-kind-txt'>{kind_word}</span> {_amount_to_str(t.amount)} د.ع.</span>
                    {photo_html}
                  </div>
                  {dot_bottom}
                  {note_html}
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
        flash_html = _flash_block(flash_key, err_msg)

        name_val = _html_escape(cust.name)
        phone_val = _html_escape((cust.phone or "").strip())

        edit_form = f"""
        <div class='cust-edit-toggle'>
          <button type='button' class='btn btn-outline' onclick="document.getElementById('cust-edit-panel').classList.toggle('hidden');">
            ✏️ تعديل معلومات العميل
          </button>
        </div>
        <div id='cust-edit-panel' class='hidden web-section'>
          <h3 class='web-h3'>✏️ بيانات العميل</h3>
          <form method='post' action='/creditbook/customer/{cust.id}/update' class='stack-form'>
            <input type='hidden' name='csrf' value='{_html_escape(csrf_u)}'/>
            <label for='edit_name'>الاسم</label>
            <input type='text' id='edit_name' name='name' required maxlength='255' value="{name_val}"/>
            <label for='edit_phone'>الهاتف (اكتب «حذف» لإزالة الرقم)</label>
            <input type='text' id='edit_phone' name='phone' value="{phone_val}" placeholder='+964 أو 077' dir='ltr'/>
            <button type='submit' class='btn btn-primary'>حفظ التعديلات</button>
          </form>
        </div>
        """

        del_form = f"""
        <div class='web-section web-danger'>
          <h3 class='web-h3'>⚠️ حذف العميل نهائياً</h3>
          <form method='post' action='/creditbook/customer/{cust.id}/delete' class='stack-form'
                onsubmit="return confirm('حذف العميل وجميع معاملاته؟ لا يمكن التراجع.');">
            <input type='hidden' name='csrf' value='{_html_escape(csrf_d)}'/>
            <button type='submit' class='btn btn-danger'>حذف العميل</button>
          </form>
        </div>
        """

        txn_form = f"""
        <div class='web-section'>
          <h3 class='web-h3'>➕ معاملة جديدة</h3>
          <p class='hint'>اختر نوع المعاملة ثم املأ المبلغ والملاحظة والتاريخ (اختياري) ويمكنك إرفاق صورة.</p>
          <div class='txn-kind-row'>
            <button type='button' class='btn btn-took' onclick="showNewTxn('took')">🔴 أخذت</button>
            <button type='button' class='btn btn-gave' onclick="showNewTxn('gave')">🟢 أعطيت</button>
          </div>
          <div id='new-txn-panel' class='hidden txn-panel'>
            <form method='post' action='/creditbook/customer/{cust.id}/txn_add' class='stack-form' enctype='multipart/form-data'
                  onsubmit="var k=document.getElementById('txn-kind-field'); if(!k||!k.value){{ alert('اضغط «أخذت» أو «أعطيت» أولاً'); return false; }} return true;">
              <input type='hidden' name='csrf' value='{_html_escape(csrf_t)}'/>
              <input type='hidden' name='kind' id='txn-kind-field' value=''/>
              <p id='txn-kind-hint' class='txn-kind-hint'></p>
              <label for='amt'>المبلغ (د.ع.)</label>
              <input type='text' id='amt' name='amount' required placeholder='مثال: 775.25' dir='ltr' autocomplete='off'/>
              <label for='tnote'>ملاحظة (اختياري)</label>
              <textarea id='tnote' name='note' rows='2' placeholder='نص أو سطران (مبلغ ثم ملاحظة)'></textarea>
              <label for='txn_dt'>تاريخ ووقت المعاملة (اختياري — إن تُرك فارغاً يُستخدم الوقت الحالي)</label>
              <input type='datetime-local' id='txn_dt' name='txn_datetime' dir='ltr'/>
              <label for='txphoto'>صورة مرفقة (اختياري)</label>
              <input type='file' id='txphoto' name='photo' accept='image/*'/>
              <button type='submit' class='btn btn-primary'>تسجيل المعاملة</button>
            </form>
          </div>
        </div>
        <script>
        function showNewTxn(kind) {{
          var p = document.getElementById('new-txn-panel');
          var f = document.getElementById('txn-kind-field');
          var h = document.getElementById('txn-kind-hint');
          if (!p || !f || !h) return;
          p.classList.remove('hidden');
          f.value = kind;
          h.textContent = kind === 'took'
            ? '🔴 أخذت — استلمت من العميل'
            : '🟢 أعطيت — سلّمت للعميل';
          p.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
        }}
        </script>
        """

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
          <body class='page-cust'>
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
              {flash_html}
              <p style='margin:6px 0 4px;font-weight:700;color:#475569'>👤 {owner_disp}</p>
              <div class='cust-meta' style='margin-bottom:8px;font-size:1.05rem;font-weight:700;color:#0f172a'>👤 {cust_meta}</div>
              <div class='cust-bal {balance_class}' style='font-size:1.25rem;margin-bottom:12px'>{balance_text} د.ع.</div>
              {edit_form}
              {txn_form}
              <h3 class='web-h3'>📜 المعاملات</h3>
              {''.join(tx_rows) if tx_rows else '<p class="hint">لا توجد معاملات بعد.</p>'}
              {more_btn}
              {del_form}
            </div>
          </body>
        </html>
        """
        return html_out
    finally:
        db.close()


def render_tx_edit_page(
    user: User,
    tx_id: int,
    owner_user_id: int,
    favicon_href: str,
    brand_img: str,
    flash_key: str | None = None,
    err_msg: str | None = None,
) -> str | None:
    """تعديل / حذف معاملة واحدة."""
    db = SessionLocal()
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            return None
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        if not cust or cust.user_id != owner_user_id:
            return None

        uid = owner_user_id
        csrf_e = csrf_token(uid, f"tx_edit_{tx_id}")
        csrf_k = csrf_token(uid, f"tx_kind_{tx_id}")
        csrf_x = csrf_token(uid, f"tx_del_{tx_id}")

        amt_s = _amount_to_str(tx.amount)
        note_s = _html_escape((tx.note or "").strip())
        kind_ar = "أعطيت 🟢" if tx.kind == "gave" else "أخذت 🔴"
        cust_link = f"/creditbook/customer/{cust.id}"
        owner_disp = _html_escape(user.full_name or user.username or "حسابي")
        flash_html = _flash_block(flash_key, err_msg)

        dt = tx.created_at
        if dt is not None and getattr(dt, "tzinfo", None):
            dt = dt.replace(tzinfo=None)
        dt_val = dt.strftime("%Y-%m-%dT%H:%M") if dt else ""

        cur_photo = ""
        if getattr(tx, "photo_file_id", None):
            pf = quote(str(tx.photo_file_id), safe="")
            cur_photo = (
                f"<p class='hint'>📷 صورة مرفقة: "
                f"<a href='/creditbook/photo-view/{pf}' target='_blank' rel='noopener'>عرض</a> "
                f"— يمكنك استبدالها أو إزالتها أدناه.</p>"
            )

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
            <title>معاملة #{tx_id}</title>
          </head>
          <body>
            <div class='card'>
              <div class='brand-header'>
                <div class='brand'>
                  <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt=''/>
                  <h2>تعديل معاملة</h2>
                </div>
              </div>
              <div class='toolbar'>
                <a class='btn btn-secondary' href='{cust_link}'>◀ {_html_escape(cust.name)}</a>
                <a class='btn btn-secondary' href='/creditbook/dashboard'>العملاء</a>
              </div>
              {flash_html}
              <p class='hint'>👤 {owner_disp} — النوع الحالي: <strong>{kind_ar}</strong></p>
              {cur_photo}
              <div class='web-section'>
                <form method='post' action='/creditbook/tx/{tx_id}/update' class='stack-form' enctype='multipart/form-data'>
                  <input type='hidden' name='csrf' value='{_html_escape(csrf_e)}'/>
                  <label for='txamt'>المبلغ (د.ع.)</label>
                  <input type='text' id='txamt' name='amount' required value='{_html_escape(amt_s)}' dir='ltr'/>
                  <label for='txnote'>الملاحظة (اكتب «حذف» لمسحها)</label>
                  <textarea id='txnote' name='note' rows='3'>{note_s}</textarea>
                  <label for='txdt'>تاريخ ووقت المعاملة</label>
                  <input type='datetime-local' id='txdt' name='txn_datetime' value='{_html_escape(dt_val)}' dir='ltr'/>
                  <label for='txphoto2'>صورة جديدة (اختياري — تستبدل الصورة الحالية)</label>
                  <input type='file' id='txphoto2' name='photo' accept='image/*'/>
                  <label class='web-check'><input type='checkbox' name='remove_photo' value='1'/> إزالة الصورة من المعاملة</label>
                  <button type='submit' class='btn btn-primary'>حفظ التعديلات</button>
                </form>
              </div>
              <div class='web-section'>
                <form method='post' action='/creditbook/tx/{tx_id}/toggle_kind' class='stack-form'
                      onsubmit="return confirm('تأكيد عكس نوع المعاملة (أعطيت ↔ أخذت)؟');">
                  <input type='hidden' name='csrf' value='{_html_escape(csrf_k)}'/>
                  <button type='submit' class='btn btn-secondary'>🔁 عكس النوع (أعطيت / أخذت)</button>
                </form>
              </div>
              <div class='web-section web-danger'>
                <form method='post' action='/creditbook/tx/{tx_id}/delete' class='stack-form'
                      onsubmit="return confirm('حذف هذه المعاملة نهائياً؟');">
                  <input type='hidden' name='csrf' value='{_html_escape(csrf_x)}'/>
                  <button type='submit' class='btn btn-danger'>🗑 حذف المعاملة</button>
                </form>
              </div>
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
