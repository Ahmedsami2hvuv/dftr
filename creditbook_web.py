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
from utils.phone import format_phone_iq_local_display, normalize_phone, same_phone, wa_number as _wa_number

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
    """رمز مرتبط بالجلسة واليوم — يبقى صالحاً عدة أيام حتى لا تنتهي الصلاحية مع الصفحة المفتوحة."""
    tb = int(time.time()) // 86400
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
    now_d = int(time.time()) // 86400
    if tb < now_d - 14 or tb > now_d + 1:
        return False
    msg = f"{uid}:{action}:{tb}".encode("utf-8")
    expected = hmac.new(web_session_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def csrf_token_public(action: str) -> str:
    """رمز للنماذج بدون جلسة (مثل التسجيل)."""
    tb = int(time.time()) // 86400
    msg = f"pub:{action}:{tb}".encode("utf-8")
    sig = hmac.new(web_session_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{tb}.{sig}"


def csrf_verify_public(action: str, token: str) -> bool:
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
    now_d = int(time.time()) // 86400
    if tb < now_d - 14 or tb > now_d + 1:
        return False
    msg = f"pub:{action}:{tb}".encode("utf-8")
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
    "acc_prof": "تم حفظ بيانات الحساب ✅",
    "acc_pwd": "تم تغيير كلمة المرور ✅",
    "reg_ok": "تم إنشاء الحساب — يمكنك تسجيل الدخول الآن ✅",
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


def render_owner_showcase_card(user: User) -> str:
    """مربع «صنع بواسطة» كما في صفحة تقرير المشاركة."""
    owner_name = (user.full_name or user.username or "صاحب الحساب").strip() or "صاحب الحساب"
    owner_phone = (user.phone or "").strip()
    owner_name_esc = _html_escape(owner_name)
    if owner_phone:
        disp = format_phone_iq_local_display(owner_phone)
        wa_p = _wa_number(owner_phone)
        wa_href = _html_escape(f"https://api.whatsapp.com/send?phone={wa_p}")
        phone_html = (
            f"<a class='owner-phone owner-phone-wa' href='{wa_href}' "
            f"target='_blank' rel='noopener' dir='ltr' title='فتح واتساب'>{_html_escape(disp)}</a>"
        )
    else:
        phone_html = "<span class='owner-phone-muted'>لم يُضف رقم بعد</span>"
    return f"""
    <div class='owner-showcase'>
      <div class='owner-badge'>صنع بواسطة</div>
      <div class='owner-name-row'>
        <div class='owner-name'>{owner_name_esc}</div>
        {phone_html}
      </div>
    </div>
    """


def wrap_creditbook_app_shell(
    user: User,
    favicon_href: str,
    brand_img: str,
    page_title: str,
    active_nav: str | None,
    card_inner: str,
    body_class: str = "",
) -> str:
    """هيكل: قائمة جانبية منزلقة (زر ☰) + المحتوى."""
    disp = _html_escape(user.full_name or user.username or "حسابي")
    acc_active = " sidebar-link-active" if active_nav == "account" else ""
    head = f"""
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
        <title>{_html_escape(page_title)}</title>
      </head>
      <body class='app-body {body_class}'>
        <button type='button' class='sidebar-menu-btn' id='sidebar-menu-btn' aria-expanded='false' aria-controls='app-sidebar' title='القائمة'>☰</button>
        <div class='sidebar-scrim' id='sidebar-scrim' hidden></div>
        <div class='app-shell'>
          <aside class='app-sidebar' id='app-sidebar' aria-label='القائمة' aria-hidden='true'>
            <div class='sidebar-inner'>
              <button type='button' class='sidebar-close-btn' id='sidebar-close-btn' title='إغلاق'>✕</button>
              <a class='sidebar-brand sidebar-close-link' href='/creditbook/dashboard' title='العملاء'>
                <img class='brand-logo-sm' src="{_html_escape(brand_img)}" width='48' height='48' alt=''/>
                <span class='sidebar-brand-txt'>دفتر الديون</span>
              </a>
              <p class='sidebar-user'>{disp}</p>
              <nav class='sidebar-nav'>
                <a class='sidebar-link{acc_active} sidebar-close-link' href='/creditbook/account'>حسابي</a>
                <a class='sidebar-link sidebar-link-logout sidebar-close-link' href='/creditbook/logout_confirm'>تسجيل الخروج</a>
              </nav>
              {f"<a class='sidebar-bot sidebar-close-link' href='https://t.me/{BOT_USERNAME}' target='_blank' rel='noopener'>البوت في تيليجرام</a>" if BOT_USERNAME else ""}
            </div>
          </aside>
          <main class='app-main'>
            <div class='card app-card'>
              {card_inner}
            </div>
          </main>
        </div>
        <script>
        (function() {{
          var b = document.body;
          var side = document.getElementById('app-sidebar');
          var scrim = document.getElementById('sidebar-scrim');
          var openBtn = document.getElementById('sidebar-menu-btn');
          var closeBtn = document.getElementById('sidebar-close-btn');
          function setOpen(on) {{
            if (on) {{ b.classList.add('sidebar-open'); scrim.hidden = false; if(openBtn) openBtn.setAttribute('aria-expanded','true'); if(side) {{ side.setAttribute('aria-hidden','false'); }} }}
            else {{ b.classList.remove('sidebar-open'); scrim.hidden = true; if(openBtn) openBtn.setAttribute('aria-expanded','false'); if(side) {{ side.setAttribute('aria-hidden','true'); }} }}
          }}
          if (openBtn) openBtn.addEventListener('click', function() {{ setOpen(!b.classList.contains('sidebar-open')); }});
          if (closeBtn) closeBtn.addEventListener('click', function() {{ setOpen(false); }});
          if (scrim) scrim.addEventListener('click', function() {{ setOpen(false); }});
          var links = document.querySelectorAll('.sidebar-close-link');
          for (var i = 0; i < links.length; i++) {{
            links[i].addEventListener('click', function() {{ setOpen(false); }});
          }}
        }})();
        </script>
      </body>
    </html>
    """
    return head


def render_logout_confirm_page(user: User, favicon_href: str, brand_img: str) -> str:
    inner = f"""
      <div class='brand-header'>
        <div class='brand'>
          <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt=''/>
          <h2>تسجيل الخروج</h2>
        </div>
      </div>
      <p class='hint'>هل تريد حقاً تسجيل الخروج من الموقع؟</p>
      <div class='toolbar' style='margin-top:16px'>
        <form method='post' action='/creditbook/logout' style='display:inline;margin:0'>
          <button type='submit' class='btn btn-danger' style='margin-top:0'>نعم، تسجيل الخروج</button>
        </form>
        <a class='btn btn-secondary' href='/creditbook/dashboard' style='margin-top:0'>إلغاء</a>
      </div>
    """
    return wrap_creditbook_app_shell(user, favicon_href, brand_img, "تسجيل الخروج", None, inner)


def render_account_page(
    user: User,
    favicon_href: str,
    brand_img: str,
    flash_key: str | None = None,
    err_msg: str | None = None,
) -> str:
    uid = user.id
    csrf_p = csrf_token(uid, "acct_profile")
    csrf_w = csrf_token(uid, "acct_pass")
    name_v = _html_escape((user.full_name or user.username or "").strip())
    phone_v = _html_escape((user.phone or "").strip())
    disp_phone = format_phone_iq_local_display((user.phone or "").strip()) if user.phone else "—"
    flash_html = _flash_block(flash_key, err_msg)
    inner = f"""
      <div class='brand-header share-report-head'>
        <div class='brand'>
          <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt=''/>
          <div class='brand-text-wrap'>
            <h2>حسابي</h2>
            <p class='brand-subtitle'>اسم المستخدم: {_html_escape(user.full_name or user.username or "—")}</p>
          </div>
        </div>
        {render_owner_showcase_card(user)}
      </div>
      {flash_html}
      <div class='web-section' style='border-top:none;padding-top:0'>
        <h3 class='web-h3'>معلومات الحساب</h3>
        <p class='acct-summary'><strong>الاسم الظاهر:</strong> {_html_escape((user.full_name or user.username or "—").strip())}</p>
        <p class='acct-summary'><strong>الهاتف:</strong> {_html_escape(disp_phone)}</p>
      </div>
      <div class='web-section'>
        <h3 class='web-h3'>تعديل الاسم والهاتف</h3>
        <form method='post' action='/creditbook/account/profile' class='stack-form'>
          <input type='hidden' name='csrf' value='{_html_escape(csrf_p)}'/>
          <label for='acc_name'>الاسم</label>
          <input type='text' id='acc_name' name='full_name' maxlength='255' value="{name_v}" placeholder='اسمك'/>
          <label for='acc_phone'>رقم الهاتف (نفس تسجيل الدخول)</label>
          <input type='tel' id='acc_phone' name='phone' value="{phone_v}" placeholder='+9647…' dir='ltr' autocomplete='tel'/>
          <button type='submit' class='btn btn-primary'>حفظ التعديلات</button>
        </form>
      </div>
      <div class='web-section'>
        <h3 class='web-h3'>تغيير كلمة المرور</h3>
        <form method='post' action='/creditbook/account/password' class='stack-form' autocomplete='off'>
          <input type='hidden' name='csrf' value='{_html_escape(csrf_w)}'/>
          <label for='old_pw'>كلمة المرور الحالية</label>
          <input type='password' id='old_pw' name='current_password' required autocomplete='current-password'/>
          <label for='new_pw'>كلمة المرور الجديدة</label>
          <input type='password' id='new_pw' name='new_password' required minlength='4' autocomplete='new-password'/>
          <label for='new_pw2'>تأكيد كلمة المرور الجديدة</label>
          <input type='password' id='new_pw2' name='new_password2' required minlength='4' autocomplete='new-password'/>
          <button type='submit' class='btn btn-primary'>تحديث كلمة المرور</button>
        </form>
      </div>
    """
    return wrap_creditbook_app_shell(user, favicon_href, brand_img, "حسابي — دفتر الديون", "account", inner)


def render_login_page(
    error: str | None,
    favicon_href: str,
    brand_img: str,
    flash_key: str | None = None,
) -> str:
    err = f"<div class='err'>{_html_escape(error)}</div>" if error else ""
    flash_html = _flash_block(flash_key, None)
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
        <div class='card login-card'>
          <div class='brand-header login-brand-head'>
            <div class='brand'>
              <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt='دفتر الديون'/>
              <div class='brand-text-wrap'>
                <h2>دفتر الديون</h2>
                <p class='brand-subtitle'>سجّل الدخول أو أنشئ حساباً جديداً — نفس رقم الهاتف وكلمة المرور كما في البوت</p>
              </div>
            </div>
          </div>
          {flash_html}
          <div class='login-split'>
            <div class='login-split-col'>
              <p class='hint' style='margin-top:0'>تسجيل الدخول</p>
              {err}
              <form class='login-form' method='post' action='/creditbook/login' autocomplete='on'>
                <label for='phone'>رقم الهاتف</label>
                <input type='tel' id='phone' name='phone' required placeholder='+9647xxxxxxxx' dir='ltr'/>
                <label for='password'>كلمة المرور</label>
                <input type='password' id='password' name='password' required autocomplete='current-password'/>
                <p style='margin-top:16px'>
                  <button type='submit' class='btn btn-primary btn-block'>تسجيل الدخول</button>
                </p>
              </form>
            </div>
            <div class='login-split-col login-register-col'>
              <p class='hint' style='margin-top:0'>ليس لديك حساب؟</p>
              <p class='login-register-lead'>أنشئ حساباً جديداً بالاسم والهاتف وكلمة المرور (بدون تيليجرام).</p>
              <a class='btn btn-register' href='/creditbook/register'>إنشاء حساب جديد</a>
            </div>
          </div>
          {f"<p style='margin-top:16px;text-align:center'><a class='btn btn-bot' href='https://t.me/{BOT_USERNAME}' target='_blank' rel='noopener'>فتح البوت في تيليجرام</a></p>" if BOT_USERNAME else ""}
        </div>
      </body>
    </html>
    """


def render_register_page(
    error: str | None,
    favicon_href: str,
    brand_img: str,
    csrf: str,
) -> str:
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
        <title>إنشاء حساب — دفتر الديون</title>
      </head>
      <body>
        <div class='card login-card'>
          <div class='brand-header'>
            <div class='brand'>
              <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt=''/>
              <div class='brand-text-wrap'>
                <h2>دفتر الديون</h2>
                <p class='brand-subtitle'>حساب جديد للوصول من المتصفح</p>
              </div>
            </div>
          </div>
          {err}
          <form class='stack-form' method='post' action='/creditbook/register' autocomplete='off'>
            <input type='hidden' name='csrf' value='{_html_escape(csrf)}'/>
            <label for='reg_name'>الاسم الكامل</label>
            <input type='text' id='reg_name' name='full_name' required maxlength='255' placeholder='الاسم الظاهر في الدفتر'/>
            <label for='reg_phone'>رقم الهاتف</label>
            <input type='tel' id='reg_phone' name='phone' required placeholder='+9647… أو 077…' dir='ltr' autocomplete='tel'/>
            <label for='reg_pw'>كلمة المرور (4 أحرف على الأقل)</label>
            <input type='password' id='reg_pw' name='password' required minlength='4' autocomplete='new-password'/>
            <label for='reg_pw2'>تأكيد كلمة المرور</label>
            <input type='password' id='reg_pw2' name='password2' required minlength='4' autocomplete='new-password'/>
            <button type='submit' class='btn btn-primary btn-block'>إنشاء الحساب</button>
          </form>
          <p style='margin-top:16px;text-align:center'>
            <a class='btn btn-secondary' href='/creditbook/login'>◀ لديك حساب؟ تسجيل الدخول</a>
          </p>
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
    body = "".join(rows) if rows else "<p class='hint'>لا يوجد عملاء بعد — اضغط «عميل جديد» لإضافة أول عميل.</p>"
    flash_html = _flash_block(flash_key, err_msg)
    add_form = f"""
    <div class='new-cust-block'>
      <button type='button' class='btn btn-primary btn-new-cust' onclick="document.getElementById('new-cust-panel').classList.toggle('hidden');">
        ➕ عميل جديد
      </button>
      <div id='new-cust-panel' class='hidden web-section new-cust-panel'>
        <h3 class='web-h3'>إضافة عميل</h3>
        <form method='post' action='/creditbook/customer/create' class='stack-form'>
          <input type='hidden' name='csrf' value='{_html_escape(csrf_c)}'/>
          <label for='cname'>الاسم</label>
          <input type='text' id='cname' name='name' required maxlength='255' placeholder='اسم العميل'/>
          <label for='cphone'>الهاتف (اختياري)</label>
          <input type='tel' id='cphone' name='phone' placeholder='+9647… أو 077…' dir='ltr'/>
          <button type='submit' class='btn btn-primary'>حفظ العميل</button>
        </form>
      </div>
    </div>
    """
    uname = _html_escape(user.full_name or user.username or "مستخدم")
    card = f"""
          <div class='brand-header share-report-head'>
            <div class='brand'>
              <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt=''/>
              <div class='brand-text-wrap'>
                <h2>دفتر الديون</h2>
                <p class='brand-subtitle'>اسم المستخدم: {uname}</p>
              </div>
            </div>
            {render_owner_showcase_card(user)}
          </div>
          {flash_html}
          <p class='hint'>إدارة العملاء والمعاملات من المتصفح أو من البوت.</p>
          {add_form}
          <h3 class='web-h3' style='margin-top:8px'>📋 عملائي</h3>
          {body}
    """
    return wrap_creditbook_app_shell(user, favicon_href, brand_img, "دفتر الديون — عملائي", None, card)


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
                f"<div class='tx-note-black'><span class='tx-note-label'>ملاحظة:</span> {_html_escape(note)}</div>"
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
            tx_rows.append(
                f"""
                <div class='tx tx-row-{t.kind}'>
                  <div class='tx-line-main'>
                    <span class='tx-kind-amt {kc}'><span class='tx-kind-txt'>{kind_word}</span> {_amount_to_str(t.amount)} د.ع.</span>
                    <span class='tx-sep' aria-hidden='true'>·</span>
                    <span class='tx-date' dir='ltr'>{dt}</span>
                    <span class='tx-sep' aria-hidden='true'>·</span>
                    <span class='tx-remain {remain_class}'>الرصيد بعدها: {_amount_to_str(remain)} د.ع.</span>
                    <span class='tx-sep' aria-hidden='true'>·</span>
                    <a class='tx-edit-btn' href='/creditbook/tx/{t.id}'><span class='tx-edit-ico'>✎</span> تعديل</a>
                    {photo_html}
                  </div>
                  {note_html}
                </div>
                """
            )

        more_btn = ""
        if has_more:
            more_btn = f"<a class='btn btn-primary' href='/creditbook/customer/{cust.id}?offset={more_offset}'>➕ عرض المزيد</a>"

        balance_class = "bal-red" if bal > 0 else ("bal-green" if bal < 0 else "")

        cust_disp_phone = format_phone_iq_local_display((cust.phone or "").strip()) if cust.phone else ""
        cust_meta = _html_escape(cust.name) + (f" — {_html_escape(cust_disp_phone)}" if cust_disp_phone else "")

        owner_disp = _html_escape(user.full_name or user.username or "حسابي")
        flash_html = _flash_block(flash_key, err_msg)

        name_val = _html_escape(cust.name)
        phone_val = _html_escape((cust.phone or "").strip())

        manage_panel = f"""
        <div class='cust-edit-toggle'>
          <button type='button' class='btn btn-primary btn-manage-cust' onclick="document.getElementById('cust-manage-panel').classList.toggle('hidden');">
            ⚙️ إدارة العميل — تعديل أو حذف
          </button>
          <p class='hint cust-manage-hint'>اضغط لفتح نموذج تعديل الاسم والهاتف أو حذف العميل نهائياً</p>
        </div>
        <div id='cust-manage-panel' class='hidden web-section cust-manage-panel'>
          <h3 class='web-h3'>تعديل بيانات العميل</h3>
          <p class='hint' style='margin-top:0'>غيّر الاسم أو رقم الهاتف، أو احذف الحقل لإزالة الرقم.</p>
          <form method='post' action='/creditbook/customer/{cust.id}/update' class='stack-form'>
            <input type='hidden' name='csrf' value='{_html_escape(csrf_u)}'/>
            <label for='edit_name'>الاسم</label>
            <input type='text' id='edit_name' name='name' required maxlength='255' value="{name_val}"/>
            <label for='edit_phone'>الهاتف (اختياري — اتركه فارغاً لإزالة الرقم)</label>
            <input type='text' id='edit_phone' name='phone' value="{phone_val}" placeholder='+964 أو 077' dir='ltr'/>
            <button type='submit' class='btn btn-primary'>حفظ التعديلات</button>
          </form>
          <div class='web-danger cust-del-inner'>
            <h3 class='web-h3'>حذف العميل نهائياً</h3>
            <p class='hint' style='margin-top:0'>يُحذف العميل وجميع معاملاته ولا يمكن التراجع.</p>
            <form method='post' action='/creditbook/customer/{cust.id}/delete' class='stack-form'
                  onsubmit="return confirm('حذف العميل وجميع معاملاته؟ لا يمكن التراجع.');">
              <input type='hidden' name='csrf' value='{_html_escape(csrf_d)}'/>
              <button type='submit' class='btn btn-danger'>حذف هذا العميل</button>
            </form>
          </div>
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

        net_line = f"{_amount_to_str(bal)} د.ع."
        card_inner = f"""
              <div class='brand-header share-report-head cust-page-head'>
                <div class='cust-head-left'>
                  <div class='brand'>
                    <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt=''/>
                    <div class='brand-text-wrap'>
                      <h2>دفتر الديون</h2>
                      <p class='brand-subtitle'>اسم المستخدم: {owner_disp}</p>
                    </div>
                  </div>
                  <div class='cust-head-stats' role='group' aria-label='إجماليات العميل'>
                    <div class='cust-stat-line'><span class='cust-stat-lbl'>أخذت الكلي</span><span class='cust-stat-val bal-red'>{_amount_to_str(took)} د.ع.</span></div>
                    <div class='cust-stat-line'><span class='cust-stat-lbl'>أعطيت الكلي</span><span class='cust-stat-val bal-green'>{_amount_to_str(gave)} د.ع.</span></div>
                    <div class='cust-stat-line'><span class='cust-stat-lbl'>النتيجة</span><span class='cust-stat-val {balance_class}'>{net_line}</span></div>
                  </div>
                  <div class='cust-line-identity'>👤 {cust_meta}</div>
                </div>
                {render_owner_showcase_card(user)}
              </div>
              <div class='toolbar'>
                <a class='btn btn-secondary' href='/creditbook/dashboard'>◀ العملاء</a>
              </div>
              {flash_html}
              {manage_panel}
              {txn_form}
              <h3 class='web-h3'>📜 المعاملات</h3>
              {''.join(tx_rows) if tx_rows else '<p class="hint">لا توجد معاملات بعد.</p>'}
              {more_btn}
        """
        return wrap_creditbook_app_shell(
            user,
            favicon_href,
            brand_img,
            f"{cust.name} — دفتر الديون",
            None,
            card_inner,
            body_class="page-cust",
        )
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

        inner = f"""
              <div class='brand-header share-report-head'>
                <div class='brand'>
                  <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt=''/>
                  <div class='brand-text-wrap'>
                    <h2>تعديل معاملة</h2>
                    <p class='brand-subtitle'>اسم المستخدم: {owner_disp}</p>
                  </div>
                </div>
                {render_owner_showcase_card(user)}
              </div>
              <div class='toolbar'>
                <a class='btn btn-secondary' href='{cust_link}'>◀ {_html_escape(cust.name)}</a>
                <a class='btn btn-secondary' href='/creditbook/dashboard'>العملاء</a>
              </div>
              {flash_html}
              <p class='hint'>النوع الحالي: <strong>{kind_ar}</strong></p>
              {cur_photo}
              <div class='web-section'>
                <form method='post' action='/creditbook/tx/{tx_id}/update' class='stack-form' enctype='multipart/form-data'>
                  <input type='hidden' name='csrf' value='{_html_escape(csrf_e)}'/>
                  <label for='txamt'>المبلغ (د.ع.)</label>
                  <input type='text' id='txamt' name='amount' required value='{_html_escape(amt_s)}' dir='ltr'/>
                  <label for='txnote'>الملاحظة (اتركها فارغة لمسحها)</label>
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
        """
        return wrap_creditbook_app_shell(user, favicon_href, brand_img, f"معاملة #{tx_id}", None, inner)
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
