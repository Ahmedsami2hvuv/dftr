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
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

from database import SessionLocal
from app_models import Customer, CustomerTransaction, User
from config import ADMIN_PHONE, BOT_USERNAME, CREDITBOOK_SHOWCASE_NAME, CREDITBOOK_SHOWCASE_PHONE, web_session_secret
from utils.password import check_password
from utils.phone import format_phone_iq_local_display, normalize_phone, same_phone, wa_number as _wa_number

SESSION_COOKIE = "dftr_web"
SESSION_DAYS = 30
TX_PAGE_SIZE = 15
REPORT_PAGE_SIZE = 25
# زيادة الرقم عند تغيير CSS حتى يُحمّل الملف الجديد بدون كاش قديم
CREDITBOOK_CSS_HREF = "/creditbook/static/creditbook_app.css?v=18"


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
    "fb_ok": "تم إرسال رسالتك إلى الإدارة عبر تيليجرام ✅",
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


def owner_display_name_for_user(user: User | None, *, empty: str = "مستخدم") -> str:
    """
    الاسم الظاهر لصاحب الدفتر: يُفضّل الاسم الكامل (حسابي / الفاتورة) ثم اسم المستخدم.
    strip() لكل حقل حتى لا يُعتبر اسمٌ من مسافات فقط اسماً صالحاً (كان يسبب «صنع بواسطة» خاطئاً).
    """
    if not user:
        return empty
    fn = (user.full_name or "").strip()
    if fn:
        return fn
    un = (user.username or "").strip()
    if un:
        return un
    return empty


def _brand_home_block(brand_img: str, user_name_display_esc: str) -> str:
    """الشعار + عنوان دفتر الديون + اسم صاحب الحساب — النقر يعيد للوحة الرئيسية."""
    return (
        f"<a class='brand-home-link' href='/creditbook/dashboard' title='العودة إلى قائمة العملاء' aria-label='العودة إلى قائمة العملاء'>"
        f"<img class='brand-logo' src=\"{_html_escape(brand_img)}\" width='64' height='64' alt=''/>"
        f"<div class='brand-text-wrap'>"
        f"<h2>{_html_escape('دفتر الديون')}</h2>"
        f"<p class='brand-user-name'>{user_name_display_esc}</p>"
        f"</div></a>"
    )


def _brand_customer_block(
    brand_img: str,
    owner_disp_esc: str,
    cust_name_esc: str,
    phone_local_display: str,
) -> str:
    """صفحة عميل: دفتر الديون ← صاحب الحساب ← اسم العميل ← الهاتف (يُخفى الهاتف وصاحب الحساب على الجوال بالـ CSS)."""
    phone_html = ""
    if (phone_local_display or "").strip():
        phone_html = (
            f"<p class='brand-cust-phone' dir='ltr'>📞 {_html_escape(phone_local_display.strip())}</p>"
        )
    return (
        f"<a class='brand-home-link' href='/creditbook/dashboard' title='العودة إلى قائمة العملاء' aria-label='العودة إلى قائمة العملاء'>"
        f"<img class='brand-logo' src=\"{_html_escape(brand_img)}\" width='64' height='64' alt='دفتر الديون'/>"
        f"<div class='brand-text-wrap'>"
        f"<h2 class='brand-h2-title'>{_html_escape('دفتر الديون')}</h2>"
        f"<p class='brand-user-name'>{owner_disp_esc}</p>"
        f"<p class='brand-cust-name-line'>{cust_name_esc}</p>"
        f"{phone_html}"
        f"</div></a>"
    )


def render_owner_showcase_card(user: User) -> str:
    """مربع «صنع بواسطة» — هوية صانع الدفتر ثابتة (CREDITBOOK_* في الإعدادات) لجميع المستخدمين."""
    _ = user  # يُبقى للتوافق مع الاستدعاءات
    owner_name_esc = _html_escape(CREDITBOOK_SHOWCASE_NAME)
    raw = (CREDITBOOK_SHOWCASE_PHONE or "").strip()
    if raw:
        disp = format_phone_iq_local_display(raw) or raw
        wa_p = _wa_number(raw)
        wa_href = _html_escape(f"https://api.whatsapp.com/send?phone={wa_p}")
        phone_html = (
            f"<a class='owner-phone owner-phone-wa creditbook-showcase-phone' href='{wa_href}' "
            f"target='_blank' rel='noopener' dir='ltr' title='فتح واتساب'>{_html_escape(disp)}</a>"
        )
    else:
        phone_html = "<span class='owner-phone-muted'>لم يُضبط رقم صانع الدفتر</span>"
    return f"""
    <div class='owner-showcase'>
      <div class='owner-badge'>صنع بواسطة</div>
      <div class='owner-name-row'>
        <div class='owner-name'>{owner_name_esc}</div>
        {phone_html}
      </div>
    </div>
    """


def _support_whatsapp_href() -> str:
    """رابط واتساب للدعم من ADMIN_PHONE في الإعدادات."""
    raw = (ADMIN_PHONE or "").strip()
    if not raw:
        return ""
    wn = _wa_number(raw)
    return f"https://wa.me/{wn}" if wn else ""


def _pwa_meta_block(brand_img: str) -> str:
    """وسوم Web App Manifest وألوان الثيم لأيقونة الشاشة الرئيسية."""
    icon = _html_escape(brand_img)
    title_esc = _html_escape("دفتر الديون")
    return (
        f"<link rel='manifest' href='/creditbook/manifest.webmanifest'/>"
        f"<meta name='theme-color' content='#0f766e'/>"
        f"<meta name='mobile-web-app-capable' content='yes'/>"
        f"<meta name='apple-mobile-web-app-capable' content='yes'/>"
        f"<meta name='apple-mobile-web-app-status-bar-style' content='default'/>"
        f"<meta name='apple-mobile-web-app-title' content='{title_esc}'/>"
        f"<link rel='apple-touch-icon' href='{icon}'/>"
    )


def _pwa_register_sw_script() -> str:
    """تسجيل Service Worker لنطاق تطبيق الدفتر."""
    return """
    <script>
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/creditbook/pwa-sw.js', { scope: '/creditbook/' }).catch(function () {});
    }
    </script>
    """


def _pwa_install_sidebar_script() -> str:
    """زر القائمة: تثبيت عبر المتصفح أو تعليمات يدوية."""
    return """
    <script>
    (function () {
      var deferredPrompt = null;
      var btn = document.getElementById('sidebarInstallPwa');
      window.addEventListener('beforeinstallprompt', function (e) {
        e.preventDefault();
        deferredPrompt = e;
        if (btn) btn.classList.add('sidebar-install-ready');
      });
      function closeSidebar() {
        document.body.classList.remove('sidebar-open');
        var scrim = document.getElementById('sidebar-scrim');
        if (scrim) scrim.hidden = true;
        var openBtn = document.getElementById('sidebar-menu-btn');
        if (openBtn) openBtn.setAttribute('aria-expanded', 'false');
        var side = document.getElementById('app-sidebar');
        if (side) side.setAttribute('aria-hidden', 'true');
      }
      function helpText() {
        return (
          'على الكمبيوتر (Chrome أو Edge): القائمة ⋮ ← «تثبيت التطبيق» أو أيقونة التحميل بجانب شريط العنوان.\\n\\n' +
          'على أندرويد: القائمة ← «إضافة إلى الشاشة الرئيسية» أو شريط التثبيت.\\n\\n' +
          'على آيفون (Safari): زر المشاركة ⟵ «إضافة إلى الشاشة الرئيسية».'
        );
      }
      if (btn) {
        btn.addEventListener('click', function () {
          var standalone =
            window.matchMedia('(display-mode: standalone)').matches ||
            (typeof window.navigator.standalone === 'boolean' && window.navigator.standalone);
          if (standalone) {
            alert('التطبيق يعمل بالفعل كنافذة مستقلة.');
            closeSidebar();
            return;
          }
          if (deferredPrompt) {
            deferredPrompt.prompt();
            deferredPrompt.userChoice.then(function () {
              deferredPrompt = null;
              if (btn) btn.classList.remove('sidebar-install-ready');
            });
            closeSidebar();
            return;
          }
          alert(helpText());
          closeSidebar();
        });
      }
    })();
    </script>
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
    disp = _html_escape(owner_display_name_for_user(user, empty="حسابي"))
    acc_active = " sidebar-link-active" if active_nav == "account" else ""
    wa_support = _support_whatsapp_href()
    support_wa_html = ""
    if wa_support:
        support_wa_html = (
            f"<a class='sidebar-link sidebar-support-wa sidebar-close-link' href='{_html_escape(wa_support)}' "
            f"target='_blank' rel='noopener'>📞 تواصل مع الدعم</a>"
        )
    head = f"""
    <!doctype html>
    <html lang='ar' dir='rtl'>
      <head>
        <meta charset='utf-8'/>
        <meta name='viewport' content='width=device-width, initial-scale=1'/>
        {_pwa_meta_block(brand_img)}
        <link rel='icon' href='{_html_escape(favicon_href)}' type='image/png'/>
        <link rel='stylesheet' href='{CREDITBOOK_CSS_HREF}'/>
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
              <a class='sidebar-brand sidebar-close-link' href='/creditbook/dashboard' title='الرئيسية — قائمة العملاء'>
                <img class='brand-logo-sm' src="{_html_escape(brand_img)}" width='48' height='48' alt=''/>
                <span class='sidebar-brand-txt'>دفتر الديون</span>
              </a>
              <p class='sidebar-user'><span class='sidebar-user-name'>{disp}</span></p>
              <nav class='sidebar-nav'>
                <a class='sidebar-link{acc_active} sidebar-close-link' href='/creditbook/account'>حسابي</a>
                <a class='sidebar-link sidebar-close-link' href='/creditbook/feedback'>💬 إرسال مشكلة أو اقتراح</a>
                {support_wa_html}
                <a class='sidebar-link sidebar-link-logout sidebar-close-link' href='/creditbook/logout_confirm'>تسجيل الخروج</a>
              </nav>
              <button type='button' class='sidebar-link sidebar-install-btn' id='sidebarInstallPwa'>📲 تحميل التطبيق</button>
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
            if (on) {{
              b.classList.add('sidebar-open');
              if (scrim) scrim.hidden = false;
              if (openBtn) openBtn.setAttribute('aria-expanded','true');
              if (side) side.setAttribute('aria-hidden','false');
            }} else {{
              b.classList.remove('sidebar-open');
              if (scrim) scrim.hidden = true;
              if (openBtn) openBtn.setAttribute('aria-expanded','false');
              if (side) side.setAttribute('aria-hidden','true');
            }}
          }}
          if (openBtn) openBtn.addEventListener('click', function(e) {{
            e.preventDefault();
            e.stopPropagation();
            setOpen(!b.classList.contains('sidebar-open'));
          }});
          if (closeBtn) closeBtn.addEventListener('click', function(e) {{
            e.preventDefault();
            e.stopPropagation();
            setOpen(false);
          }});
          if (scrim) scrim.addEventListener('click', function() {{ setOpen(false); }});
          var links = document.querySelectorAll('.sidebar-close-link');
          for (var i = 0; i < links.length; i++) {{
            links[i].addEventListener('click', function() {{ setOpen(false); }});
          }}
        }})();
        </script>
        {_pwa_register_sw_script()}
        {_pwa_install_sidebar_script()}
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
            <p class='brand-user-name'>{_html_escape(owner_display_name_for_user(user, empty="—"))}</p>
          </div>
        </div>
        {render_owner_showcase_card(user)}
      </div>
      {flash_html}
      <div class='web-section' style='border-top:none;padding-top:0'>
        <h3 class='web-h3'>معلومات الحساب</h3>
        <p class='acct-summary'><strong>الاسم الظاهر:</strong> {_html_escape(owner_display_name_for_user(user, empty="—"))}</p>
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


def render_feedback_page(
    user: User,
    favicon_href: str,
    brand_img: str,
    flash_key: str | None = None,
    err_msg: str | None = None,
) -> str:
    """إرسال مشكلة أو اقتراح إلى الإدارة عبر تيليجرام."""
    uid = user.id
    csrf_f = csrf_token(uid, "feedback_web")
    flash_html = _flash_block(flash_key, err_msg)
    inner = f"""
      <div class='brand-header share-report-head'>
        <div class='brand'>
          <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt=''/>
          <div class='brand-text-wrap'>
            <h2>مشكلة أو اقتراح</h2>
            <p class='brand-user-name'>{_html_escape(owner_display_name_for_user(user, empty="—"))}</p>
          </div>
        </div>
        {render_owner_showcase_card(user)}
      </div>
      {flash_html}
      <div class='web-section'>
        <p class='hint'>تُرسل رسالتك إلى الإدارة على تيليجرام.</p>
        <form method='post' action='/creditbook/feedback' class='stack-form'>
          <input type='hidden' name='csrf' value='{_html_escape(csrf_f)}'/>
          <fieldset class='report-fs feedback-kind-fs'>
            <legend>النوع</legend>
            <label class='report-opt'><input type='radio' name='kind' value='problem' checked/> مشكلة</label>
            <label class='report-opt'><input type='radio' name='kind' value='suggestion'/> اقتراح</label>
          </fieldset>
          <label for='fb_text'>النص</label>
          <textarea id='fb_text' name='message' rows='6' required maxlength='3500' placeholder='اكتب التفاصيل هنا…'></textarea>
          <button type='submit' class='btn btn-primary'>إرسال</button>
        </form>
      </div>
      <div class='toolbar'>
        <a class='btn btn-secondary' href='/creditbook/dashboard'>◀ رجوع لـ العملاء</a>
      </div>
    """
    return wrap_creditbook_app_shell(
        user,
        favicon_href,
        brand_img,
        "مشكلة أو اقتراح — دفتر الديون",
        None,
        inner,
        body_class="page-feedback",
    )


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
        {_pwa_meta_block(brand_img)}
        <link rel='icon' href='{_html_escape(favicon_href)}' type='image/png'/>
        <link rel='stylesheet' href='{CREDITBOOK_CSS_HREF}'/>
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
          <p class='hint' style='margin-top:0'>تسجيل الدخول</p>
          {err}
          <form class='login-form' method='post' action='/creditbook/login' autocomplete='on'>
            <label for='phone'>رقم الهاتف</label>
            <input type='tel' id='phone' name='phone' required placeholder='+9647xxxxxxxx' dir='ltr'/>
            <label for='password'>كلمة المرور</label>
            <input type='password' id='password' name='password' required autocomplete='current-password'/>
            <div class='login-actions-row' role='group' aria-label='تسجيل الدخول أو إنشاء حساب'>
              <button type='submit' class='btn btn-primary login-btn-main'>تسجيل الدخول</button>
              <a class='login-btn-register' href='/creditbook/register'>إنشاء حساب جديد</a>
            </div>
          </form>
          <p class='login-register-note'>ليس لديك حساب؟ اضغط «إنشاء حساب جديد» بجانب زر الدخول — بالاسم والهاتف وكلمة المرور (بدون تيليجرام).</p>
          {f"<p style='margin-top:16px;text-align:center'><a class='btn btn-bot' href='https://t.me/{BOT_USERNAME}' target='_blank' rel='noopener'>فتح البوت في تيليجرام</a></p>" if BOT_USERNAME else ""}
        </div>
        {_pwa_register_sw_script()}
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
        {_pwa_meta_block(brand_img)}
        <link rel='icon' href='{_html_escape(favicon_href)}' type='image/png'/>
        <link rel='stylesheet' href='{CREDITBOOK_CSS_HREF}'/>
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
        {_pwa_register_sw_script()}
      </body>
    </html>
    """


def render_dashboard_html(
    user: User,
    favicon_href: str,
    brand_img: str,
    flash_key: str | None = None,
    err_msg: str | None = None,
    search_q: str | None = None,
    search_scope: str | None = None,
) -> str:
    uid = user.id
    csrf_c = csrf_token(uid, "cust_create")
    sc = (search_scope or "all").lower()
    if sc not in ("all", "cust", "txn"):
        sc = "all"
    body = render_dashboard_customer_rows_html(uid, search_q, sc)
    flash_html = _flash_block(flash_key, err_msg)
    add_form = f"""
    <div class='new-cust-block'>
      <div class='new-cust-actions'>
        <button type='button' class='btn btn-primary btn-new-cust' onclick="document.getElementById('new-cust-panel').classList.toggle('hidden');">
          ➕ عميل جديد
        </button>
        <a class='btn btn-secondary btn-report' href='/creditbook/report'>📊 تقرير</a>
      </div>
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
    tot_gave, tot_took, tot_net = load_dashboard_aggregate_totals(user.id)
    net_class = "bal-green" if tot_net > 0 else ("bal-red" if tot_net < 0 else "")
    uname = _html_escape(owner_display_name_for_user(user))
    q_esc = _html_escape(search_q or "")
    sel = lambda v: " selected" if sc == v else ""
    clear_search = (
        "<button type='button' class='dash-search-clear-inline' id='dash-q-clear' hidden "
        "aria-label='مسح' title='مسح'>✕</button>"
    )
    card = f"""
          <div class='brand-header share-report-head dashboard-head'>
            <div class='dashboard-brand-col'>
              <div class='brand'>
                {_brand_home_block(brand_img, uname)}
              </div>
            </div>
            <div class='cust-head-stats dashboard-totals dashboard-stats-col' role='group' aria-label='إجمالي كل العملاء'>
                <div class='cust-stat-line'><span class='cust-stat-lbl'>أخذت</span><span class='cust-stat-val bal-red'>{_amount_to_str(tot_took)} د.ع.</span></div>
                <div class='cust-stat-line'><span class='cust-stat-lbl'>أعطيت</span><span class='cust-stat-val bal-green'>{_amount_to_str(tot_gave)} د.ع.</span></div>
                <div class='cust-stat-line'><span class='cust-stat-lbl'>النتيجة</span><span class='cust-stat-val {net_class}'>{_amount_to_str(tot_net)} د.ع.</span></div>
            </div>
            <div class='dashboard-showcase-col'>
              {render_owner_showcase_card(user)}
            </div>
          </div>
          {flash_html}
          <p class='hint hint-dashboard-note'>إدارة العملاء والمعاملات من المتصفح أو من البوت.</p>
          {add_form}
          <div class='dashboard-cust-heading-row'>
            <h3 class='web-h3 dashboard-cust-title'>📋 عملائي</h3>
            <div class='dashboard-search-inline' role='search'>
              <label class='visually-hidden' for='dash-q'>تصفية العملاء والمعاملات</label>
              <select id='dash-scope' class='dash-scope-select' aria-label='نطاق التصفية'>
                <option value='all'{sel("all")}>الكل</option>
                <option value='cust'{sel("cust")}>أسماء فقط</option>
                <option value='txn'{sel("txn")}>معاملات فقط</option>
              </select>
              <div class='dashboard-search-field-wrap'>
                <input type='search' id='dash-q' name='q' value='{q_esc}' placeholder='اسم، هاتف، ملاحظة، مبلغ…' dir='auto' autocomplete='off' class='dash-search-input'/>
                {clear_search}
              </div>
            </div>
          </div>
          <div id='cust-list'>{body}</div>
          <script>
          (function() {{
            var inp = document.getElementById('dash-q');
            var list = document.getElementById('cust-list');
            var clr = document.getElementById('dash-q-clear');
            var scopeEl = document.getElementById('dash-scope');
            if (!inp || !list) return;
            var t = null;
            function scopeVal() {{ return (scopeEl && scopeEl.value) ? scopeEl.value : 'all'; }}
            function showClear() {{
              var v = (inp.value || '').trim();
              if (clr) clr.hidden = !v;
            }}
            function load() {{
              var q = (inp.value || '').trim();
              var sc = scopeVal();
              fetch('/creditbook/search_customers?q=' + encodeURIComponent(q) + '&scope=' + encodeURIComponent(sc), {{ credentials: 'same-origin' }})
                .then(function(r) {{ return r.json(); }})
                .then(function(data) {{
                  if (data && data.html !== undefined) list.innerHTML = data.html;
                }})
                .catch(function() {{}});
            }}
            function debounce() {{
              clearTimeout(t);
              t = setTimeout(function() {{ load(); showClear(); }}, 280);
            }}
            inp.addEventListener('input', debounce);
            inp.addEventListener('search', debounce);
            if (scopeEl) scopeEl.addEventListener('change', debounce);
            if (clr) clr.addEventListener('click', function() {{
              inp.value = '';
              debounce();
            }});
            showClear();
          }})();
          </script>
    """
    return wrap_creditbook_app_shell(user, favicon_href, brand_img, "دفتر الديون — عملائي", None, card, body_class="page-dashboard")


def render_report_all_transactions_page(
    user: User,
    rows: list[tuple[CustomerTransaction, Customer]],
    offset: int,
    has_more: bool,
    favicon_href: str,
    brand_img: str,
    *,
    time_order: str = "all",
    amount_filter: str = "all",
    on_date: str = "",
    search_sq: str = "",
) -> str:
    """جميع معاملات كل العملاء مع فلاتر وترقيم صفحات."""
    tot_gave, tot_took, tot_net = load_dashboard_aggregate_totals(user.id)
    net_class = "bal-green" if tot_net > 0 else ("bal-red" if tot_net < 0 else "")
    uname = _html_escape(owner_display_name_for_user(user))
    tx_rows = []
    for t, cust in rows:
        dt = t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else ""
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
        kc = _owner_kind_class(t.kind)
        kind_word = _owner_kind_word(t.kind)
        cust_link = f"/creditbook/customer/{cust.id}"
        tx_rows.append(
            f"""
            <div class='tx tx-row-{t.kind}'>
              <div class='tx-line-main'>
                <a class='report-cust-name' href='{cust_link}'>{_html_escape(cust.name)}</a>
                <span class='tx-sep' aria-hidden='true'>·</span>
                <span class='tx-kind-amt {kc}'><span class='tx-kind-txt'>{kind_word}</span> {_amount_to_str(t.amount)} د.ع.</span>
                <span class='tx-sep' aria-hidden='true'>·</span>
                <span class='tx-date' dir='ltr'>{dt}</span>
                <span class='tx-sep' aria-hidden='true'>·</span>
                <a class='tx-edit-btn' href='/creditbook/tx/{t.id}'><span class='tx-edit-ico'>✎</span> تعديل</a>
                {photo_html}
              </div>
              {note_html}
            </div>
            """
        )
    to = (time_order or "all").lower()
    if to not in ("new", "old", "all"):
        to = "all"
    af = (amount_filter or "all").lower()
    if af not in ("all", "high", "low"):
        af = "all"
    ds_raw = (on_date or "").strip()[:10]
    date_val = _html_escape(ds_raw) if ds_raw else ""
    sq_raw = (search_sq or "").strip()
    sq_esc = _html_escape(sq_raw)
    chk = lambda cur, val: " checked" if cur == val else ""

    more_btn = ""
    if has_more:
        next_off = offset + REPORT_PAGE_SIZE
        nq = report_filters_query_string(next_off, to, af, on_date or "", sq_raw)
        more_btn = f"<a class='btn btn-primary' href='/creditbook/report?{nq}'>➕ عرض المزيد</a>"
    count_note = f"<p class='hint'>عرض {offset + 1}–{offset + len(rows)}</p>" if rows else ""
    filter_hint = []
    if ds_raw:
        filter_hint.append(f"يوم {_html_escape(ds_raw)}")
    if sq_raw:
        filter_hint.append(f"تصفية: {_html_escape(sq_raw)}")
    if af == "high":
        filter_hint.append("ترتيب: المبلغ الأكبر أولاً")
    elif af == "low":
        filter_hint.append("ترتيب: المبلغ الأصغر أولاً")
    elif to == "old":
        filter_hint.append("ترتيب: الأقدم أولاً")
    elif to == "all":
        filter_hint.append("كل التقارير (الأحدث أولاً)")
    else:
        filter_hint.append("ترتيب: الأحدث أولاً")
    filter_hint_s = " — ".join(filter_hint) if filter_hint else ""
    default_filter_hint = "تُحدَّث النتائج تلقائياً عند تغيير الفلتر. استخدم «عرض المزيد» لصفحات إضافية."

    inner = f"""
          <div class='brand-header share-report-head dashboard-head'>
            <div class='dashboard-brand-col'>
              <div class='brand'>
                {_brand_home_block(brand_img, uname)}
              </div>
            </div>
            <div class='cust-head-stats dashboard-totals dashboard-stats-col' role='group' aria-label='إجمالي كل العملاء'>
                <div class='cust-stat-line'><span class='cust-stat-lbl'>أخذت</span><span class='cust-stat-val bal-red'>{_amount_to_str(tot_took)} د.ع.</span></div>
                <div class='cust-stat-line'><span class='cust-stat-lbl'>أعطيت</span><span class='cust-stat-val bal-green'>{_amount_to_str(tot_gave)} د.ع.</span></div>
                <div class='cust-stat-line'><span class='cust-stat-lbl'>النتيجة</span><span class='cust-stat-val {net_class}'>{_amount_to_str(tot_net)} د.ع.</span></div>
            </div>
            <div class='dashboard-showcase-col'>
              {render_owner_showcase_card(user)}
            </div>
          </div>
          <div class='toolbar'>
            <a class='btn btn-secondary' href='/creditbook/dashboard'>◀ رجوع لـ العملاء</a>
          </div>
          <h3 class='web-h3'>📊 تقرير — جميع المعاملات</h3>
          <form class='report-filters web-section' method='get' action='/creditbook/report'>
            <input type='hidden' name='offset' value='0'/>
            <p class='report-filters-title'>فلتر التقرير</p>
            <div class='report-filters-grid'>
              <fieldset class='report-fs'>
                <legend>الترتيب حسب التاريخ</legend>
                <label class='report-opt'><input type='radio' name='time' value='all'{chk(to, "all")}/> كل التقارير</label>
                <label class='report-opt'><input type='radio' name='time' value='new'{chk(to, "new")}/> الجديدة أولاً</label>
                <label class='report-opt'><input type='radio' name='time' value='old'{chk(to, "old")}/> القديمة أولاً</label>
              </fieldset>
              <fieldset class='report-fs'>
                <legend>المبالغ</legend>
                <label class='report-opt'><input type='radio' name='amt' value='all'{chk(af, "all")}/> كل المبالغ</label>
                <label class='report-opt'><input type='radio' name='amt' value='high'{chk(af, "high")}/> المبلغ الأكبر أولاً</label>
                <label class='report-opt'><input type='radio' name='amt' value='low'{chk(af, "low")}/> المبلغ الأصغر أولاً</label>
              </fieldset>
              <div class='report-date-wrap report-date-ltr' dir='ltr' lang='en'>
                <label for='rep-date'>تاريخ محدد (اختياري)</label>
                <input type='date' id='rep-date' name='date' value='{date_val}'/>
                <p class='hint report-date-hint' dir='rtl'>إظهار معاملات هذا اليوم فقط (حسب وقت التسجيل في الدفتر).</p>
              </div>
              <div class='report-search-wrap'>
                <label for='rep-sq'>تصفية النتائج</label>
                <input type='search' id='rep-sq' name='sq' value='{sq_esc}' placeholder='اسم عميل، ملاحظة، مبلغ…' dir='auto' autocomplete='off'/>
                <p class='hint'>تصفية الصفوف المعروضة حسب الاسم أو الملاحظة أو المبلغ.</p>
              </div>
            </div>
            <div class='report-filters-actions'>
              <a class='btn btn-secondary' href='/creditbook/report'>إعادة ضبط</a>
            </div>
          </form>
          <script>
          (function() {{
            var form = document.querySelector('form.report-filters');
            if (!form) return;
            var sq = document.getElementById('rep-sq');
            var repDate = document.getElementById('rep-date');
            var timer;
            function submitForm() {{
              var off = form.querySelector('input[name="offset"]');
              if (off) off.value = '0';
              form.submit();
            }}
            function debounceSq() {{
              clearTimeout(timer);
              timer = setTimeout(submitForm, 350);
            }}
            if (sq) {{
              sq.addEventListener('input', debounceSq);
              sq.addEventListener('search', debounceSq);
            }}
            form.querySelectorAll('input[type="radio"]').forEach(function(el) {{
              el.addEventListener('change', submitForm);
            }});
            if (repDate) repDate.addEventListener('change', submitForm);
          }})();
          </script>
          <p class='hint'>{_html_escape(filter_hint_s) if filter_hint_s else _html_escape(default_filter_hint)}</p>
          {count_note}
          {''.join(tx_rows) if tx_rows else '<p class="hint">لا توجد معاملات مطابقة.</p>'}
          {more_btn}
    """
    return wrap_creditbook_app_shell(
        user,
        favicon_href,
        brand_img,
        "تقرير المعاملات — دفتر الديون",
        None,
        inner,
        body_class="page-report",
    )


def render_customer_share_page(
    user: User,
    customer_id: int,
    view_url: str,
    wa_url: str,
    using_web: bool,
    share_preview_plain: str,
    favicon_href: str,
    brand_img: str,
) -> str:
    """صفحة أزرار المشاركة (نفس أزرار البوت بعد cust_share)."""
    owner_disp = _html_escape(owner_display_name_for_user(user, empty="حسابي"))
    warn = ""
    if not using_web:
        warn = (
            "<p class='hint share-warn'>⚠️ ملاحظة: لم يتم العثور على دومين ويب عام، "
            "لذلك الرابط احتياطي داخل تليجرام.</p>"
        )
    inner = f"""
      <div class='brand-header share-report-head'>
        <div class='brand'>
          <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt=''/>
          <div class='brand-text-wrap'>
            <h2>مشاركة 📤</h2>
            <p class='brand-user-name'>{owner_disp}</p>
          </div>
        </div>
        {render_owner_showcase_card(user)}
      </div>
      <p class='hint' style='margin-top:0'>استخدم الأزرار أدناه — نفس رسالة البوت.</p>
      {warn}
      <pre class='share-preview-box'>{_html_escape(share_preview_plain)}</pre>
      <div class='toolbar share-toolbar'>
        <a class='btn btn-primary' href="{_html_escape(view_url)}" target='_blank' rel='noopener'>فتح صفحة المعاملات</a>
        <a class='btn btn-wa' href="{_html_escape(wa_url)}" target='_blank' rel='noopener'>فتح واتساب وإرسال الرسالة</a>
      </div>
      <div class='toolbar'>
        <a class='btn btn-secondary' href='/creditbook/customer/{customer_id}'>◀ رجوع للعميل</a>
      </div>
    """
    return wrap_creditbook_app_shell(
        user,
        favicon_href,
        brand_img,
        "مشاركة — دفتر الديون",
        None,
        inner,
        body_class="page-share",
    )


def _owner_kind_word(kind: str) -> str:
    """نص النوع بدون الرمز (الرمز يُعرض في سطر منفصل أعلى/أسفل حسب الحالة)."""
    return "أعطيت" if kind == "gave" else "أخذت"


def _owner_kind_class(kind: str) -> str:
    return "bal-green" if kind == "gave" else "bal-red"


def _customer_tx_list_html_fragment(
    db,
    cust: Customer,
    owner_user_id: int,
    search_q: str | None,
    offset: int,
) -> str:
    """قائمة معاملات العميل + زر المزيد — للصفحة الكاملة وللبحث الفوري (JSON)."""
    from sqlalchemy import String, cast, or_

    sq = (search_q or "").strip()
    tx_base = db.query(CustomerTransaction).filter(CustomerTransaction.customer_id == cust.id)
    if sq:
        like_pat = f"%{sq}%"
        tx_base = tx_base.filter(
            or_(
                CustomerTransaction.note.ilike(like_pat),
                cast(CustomerTransaction.amount, String).ilike(like_pat),
            )
        )

    total = tx_base.count()

    txs = (
        tx_base.order_by(CustomerTransaction.created_at.desc())
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
    gave = sum(float(t.amount or 0) for t in cust.transactions if t.kind == "gave")
    took = sum(float(t.amount or 0) for t in cust.transactions if t.kind == "took")
    bal = gave - took

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
        remain_class = "bal-green" if remain > 0 else ("bal-red" if remain < 0 else "")
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

    q_url = quote(sq, safe="") if sq else ""
    q_suffix = f"&q={q_url}" if sq else ""

    more_btn = ""
    if has_more:
        more_btn = f"<a class='btn btn-primary' href='/creditbook/customer/{cust.id}?offset={more_offset}{q_suffix}'>➕ عرض المزيد</a>"

    empty_tx_hint = (
        "<p class='hint'>لا توجد معاملات مطابقة.</p>"
        if sq and not tx_rows
        else ('<p class="hint">لا توجد معاملات بعد.</p>' if not tx_rows else "")
    )
    body = ("".join(tx_rows) if tx_rows else empty_tx_hint) + more_btn
    return body


def render_customer_tx_list_fragment(
    owner_user_id: int,
    customer_id: int,
    search_q: str | None,
    offset: int = 0,
) -> str | None:
    """HTML لقائمة معاملات عميل — للاستجابة JSON؛ None إن لم يُعثر على العميل."""
    db = SessionLocal()
    try:
        cust = (
            db.query(Customer)
            .filter(Customer.id == customer_id, Customer.user_id == owner_user_id)
            .first()
        )
        if not cust:
            return None
        return _customer_tx_list_html_fragment(db, cust, owner_user_id, search_q, offset)
    finally:
        db.close()


def render_owner_customer_page(
    user: User,
    customer_id: int,
    owner_user_id: int,
    offset: int,
    favicon_href: str,
    brand_img: str,
    flash_key: str | None = None,
    err_msg: str | None = None,
    search_q: str | None = None,
) -> str | None:
    """صفحة عميل من منظور صاحب الدفتر + نماذج التعديل. search_q يصفّي قائمة المعاملات (ملاحظة أو مبلغ)."""
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

        sq = (search_q or "").strip()
        q_esc = _html_escape(sq)
        cust_tx_list_html = _customer_tx_list_html_fragment(db, cust, owner_user_id, search_q, offset)

        balance_class = "bal-green" if bal > 0 else ("bal-red" if bal < 0 else "")

        cust_disp_phone = format_phone_iq_local_display((cust.phone or "").strip()) if cust.phone else ""

        owner_disp = _html_escape(owner_display_name_for_user(user, empty="حسابي"))
        flash_html = _flash_block(flash_key, err_msg)

        name_val = _html_escape(cust.name)
        phone_val = _html_escape((cust.phone or "").strip())

        manage_panel = f"""
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
                  onsubmit="return confirm('هل أنت متأكد؟ سيتم حذف هذا العميل وجميع معاملاته نهائياً ولا يمكن التراجع.');">
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
        search_block = f"""
              <div class='cust-tx-search' role='search' data-cust-id='{cust.id}'>
                <label class='visually-hidden' for='cust-tx-q'>تصفية معاملات هذا العميل</label>
                <div class='cust-search-field-wrap{" cust-search-field-wrap--has-clear" if sq else ""}' id='cust-search-field-wrap'>
                  <input type='search' id='cust-tx-q' name='q' value='{q_esc}' placeholder='ملاحظة، مبلغ…' dir='auto' autocomplete='off'/>
                  <button type='button' class='cust-search-clear-inline' id='cust-tx-clear' aria-label='مسح' title='مسح'{" hidden" if not sq else ""}>✕</button>
                </div>
              </div>
        """
        cust_tx_live_script = f"""
        <script>
        (function() {{
          var root = document.querySelector('.cust-tx-search[data-cust-id="{cust.id}"]');
          var list = document.getElementById('cust-tx-list');
          var inp = document.getElementById('cust-tx-q');
          var clearBtn = document.getElementById('cust-tx-clear');
          var fieldWrap = document.getElementById('cust-search-field-wrap');
          if (!root || !list || !inp) return;
          var cid = root.getAttribute('data-cust-id');
          var timer;
          function syncClear() {{
            var v = (inp.value || '').trim();
            if (clearBtn) clearBtn.hidden = !v;
            if (fieldWrap) fieldWrap.classList.toggle('cust-search-field-wrap--has-clear', !!v);
          }}
          function pushUrl(q) {{
            try {{
              var base = '/creditbook/customer/' + cid;
              var url = q ? base + '?q=' + encodeURIComponent(q) : base;
              if (history.replaceState) history.replaceState(null, '', url);
            }} catch (e) {{}}
          }}
          function fetchFrag() {{
            var q = (inp.value || '').trim();
            syncClear();
            pushUrl(q);
            list.classList.add('cust-tx-list--loading');
            fetch('/creditbook/customer/' + cid + '/tx_search?q=' + encodeURIComponent(q), {{ credentials: 'same-origin' }})
              .then(function(r) {{ if (!r.ok) throw new Error(); return r.json(); }})
              .then(function(data) {{ list.innerHTML = data.html || ''; }})
              .catch(function() {{}})
              .finally(function() {{ list.classList.remove('cust-tx-list--loading'); }});
          }}
          function debounced() {{
            clearTimeout(timer);
            timer = setTimeout(fetchFrag, 320);
          }}
          inp.addEventListener('input', debounced);
          inp.addEventListener('search', debounced);
          if (clearBtn) clearBtn.addEventListener('click', function() {{
            inp.value = '';
            syncClear();
            fetchFrag();
          }});
          syncClear();
        }})();
        </script>
        """

        card_inner = f"""
              <div class='brand-header share-report-head dashboard-head cust-page-head'>
                <div class='dashboard-brand-col'>
                  <div class='brand'>
                    {_brand_customer_block(brand_img, owner_disp, _html_escape(cust.name), cust_disp_phone)}
                  </div>
                </div>
                <div class='cust-head-stats dashboard-stats-col' role='group' aria-label='إجماليات العميل'>
                    <div class='cust-stat-line'><span class='cust-stat-lbl'>أخذت</span><span class='cust-stat-val bal-red'>{_amount_to_str(took)} د.ع.</span></div>
                    <div class='cust-stat-line'><span class='cust-stat-lbl'>أعطيت</span><span class='cust-stat-val bal-green'>{_amount_to_str(gave)} د.ع.</span></div>
                    <div class='cust-stat-line'><span class='cust-stat-lbl'>النتيجة</span><span class='cust-stat-val {balance_class}'>{net_line}</span></div>
                </div>
                <div class='dashboard-showcase-col'>
                  {render_owner_showcase_card(user)}
                </div>
              </div>
              <div class='toolbar toolbar-cust-top'>
                <a class='btn btn-secondary' href='/creditbook/dashboard'>◀ رجوع لـ العملاء</a>
                <a class='btn btn-primary btn-cust-share' href='/creditbook/customer/{cust.id}/share'>📤 مشاركة</a>
                <button type='button' class='btn btn-secondary btn-manage-compact' onclick="var p=document.getElementById('cust-manage-panel'); if(p){{ p.classList.toggle('hidden'); if(!p.classList.contains('hidden')) p.scrollIntoView({{behavior:'smooth',block:'nearest'}}); }}">✎ تعديل العميل</button>
              </div>
              {flash_html}
              {manage_panel}
              {txn_form}
              <h3 class='web-h3'>📜 المعاملات</h3>
              {search_block}
              <div id='cust-tx-list' class='cust-tx-list'>
              {cust_tx_list_html}
              </div>
              {cust_tx_live_script}
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
        owner_disp = _html_escape(owner_display_name_for_user(user, empty="حسابي"))
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
                  <a class='brand-home-link' href='/creditbook/dashboard' title='العودة إلى قائمة العملاء' aria-label='العودة إلى قائمة العملاء'>
                    <img class='brand-logo' src="{_html_escape(brand_img)}" width='64' height='64' alt='دفتر الديون'/>
                    <div class='brand-text-wrap'>
                      <h2>تعديل معاملة</h2>
                      <p class='brand-user-name'>{owner_disp}</p>
                    </div>
                  </a>
                </div>
                {render_owner_showcase_card(user)}
              </div>
              <div class='toolbar'>
                <a class='btn btn-secondary' href='{cust_link}'>◀ رجوع لـ العميل</a>
                <a class='btn btn-secondary' href='/creditbook/dashboard'>◀ رجوع لـ العملاء</a>
              </div>
              {flash_html}
              <div class='tx-kind-edit-row'>
                <div class='tx-kind-edit-label'>
                  <span class='hint' style='margin:0'>نوع المعاملة:</span>
                  <strong class='tx-kind-current'>{kind_ar}</strong>
                </div>
                <form method='post' action='/creditbook/tx/{tx_id}/toggle_kind' class='tx-kind-toggle-form'
                      onsubmit="return confirm('هل أنت متأكد من تغيير نوع المعاملة بين أعطيت وأخذت؟');">
                  <input type='hidden' name='csrf' value='{_html_escape(csrf_k)}'/>
                  <button type='submit' class='btn btn-secondary btn-tx-toggle-kind'>🔁 تغيير النوع</button>
                </form>
              </div>
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
              <div class='web-section web-danger'>
                <form method='post' action='/creditbook/tx/{tx_id}/delete' class='stack-form'
                      onsubmit="return confirm('هل أنت متأكد من حذف هذه المعاملة نهائياً؟ لا يمكن التراجع.');">
                  <input type='hidden' name='csrf' value='{_html_escape(csrf_x)}'/>
                  <button type='submit' class='btn btn-danger'>🗑 حذف المعاملة</button>
                </form>
              </div>
        """
        return wrap_creditbook_app_shell(
            user, favicon_href, brand_img, f"معاملة #{tx_id}", None, inner, body_class="page-tx-edit"
        )
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


def load_dashboard_rows(
    user_id: int,
    q: str | None = None,
    scope: str | None = None,
) -> list[tuple[Customer, float]]:
    """قائمة العملاء مع الرصيد. q يُصفّى حسب scope: all (كل شيء)، cust (اسم/هاتف)، txn (معاملات فقط)."""
    from sqlalchemy import String, cast, or_

    q = (q or "").strip()
    sc = (scope or "all").lower()
    if sc not in ("all", "cust", "txn"):
        sc = "all"
    db = SessionLocal()
    try:
        base = db.query(Customer).filter(Customer.user_id == user_id)
        if q:
            like_pat = f"%{q}%"
            tx_match = (
                db.query(CustomerTransaction.customer_id)
                .join(Customer, Customer.id == CustomerTransaction.customer_id)
                .filter(Customer.user_id == user_id)
                .filter(
                    or_(
                        CustomerTransaction.note.ilike(like_pat),
                        cast(CustomerTransaction.amount, String).ilike(like_pat),
                    )
                )
            )
            cust_ids = {r[0] for r in tx_match.distinct().all()}
            if sc == "cust":
                base = base.filter(
                    or_(Customer.name.ilike(like_pat), Customer.phone.ilike(like_pat))
                )
            elif sc == "txn":
                if not cust_ids:
                    return []
                base = base.filter(Customer.id.in_(cust_ids))
            else:
                conds = [Customer.name.ilike(like_pat), Customer.phone.ilike(like_pat)]
                if cust_ids:
                    conds.append(Customer.id.in_(cust_ids))
                base = base.filter(or_(*conds))
        customers = base.order_by(Customer.name.asc()).all()
        out = []
        for c in customers:
            gave = sum(float(t.amount or 0) for t in c.transactions if t.kind == "gave")
            took = sum(float(t.amount or 0) for t in c.transactions if t.kind == "took")
            out.append((c, gave - took))
        return out
    finally:
        db.close()


def render_dashboard_customer_rows_html(
    user_id: int,
    q: str | None,
    scope: str | None = None,
) -> str:
    """قائمة عملاء HTML فقط — للوحة التحكم وللبحث الفوري."""
    customers = load_dashboard_rows(user_id, q, scope)
    rows = []
    for c, bal in customers:
        bc = "bal-green" if bal > 0 else ("bal-red" if bal < 0 else "")
        rows.append(
            f"""
            <a class='cust-row' href='/creditbook/customer/{c.id}'>
              <div class='cust-name'>{_html_escape(c.name)}</div>
              <div class='cust-bal {bc}'>الرصيد الحالي: {_amount_to_str(bal)} د.ع.</div>
            </a>
            """
        )
    if not rows:
        if (q or "").strip():
            return "<p class='hint'>لا يوجد عملاء مطابقين.</p>"
        return "<p class='hint'>لا يوجد عملاء بعد — اضغط «عميل جديد» لإضافة أول عميل.</p>"
    return "".join(rows)


def report_filters_query_string(
    offset: int,
    time_order: str,
    amount_filter: str,
    on_date: str,
    search_q: str = "",
) -> str:
    """معاملات GET لصفحة التقرير (للروابط و«عرض المزيد»)."""
    p: dict[str, str] = {
        "offset": str(max(0, offset)),
        "time": time_order,
        "amt": amount_filter,
    }
    d = (on_date or "").strip()[:10]
    if d:
        p["date"] = d
    sq = (search_q or "").strip()
    if sq:
        p["sq"] = sq
    return urlencode(p)


def load_all_transactions_page(
    user_id: int,
    offset: int,
    limit: int,
    *,
    time_order: str = "all",
    amount_filter: str = "all",
    on_date: str | None = None,
    search_q: str | None = None,
) -> tuple[list[tuple[CustomerTransaction, Customer]], bool]:
    """
    صفحة من جميع المعاملات مع فلاتر.
    time_order: new | old | all (كل = الأحدث أولاً مثل new)
    amount_filter: all | high | low — عند high/low يُرتّب حسب المبلغ
    on_date: YYYY-MM-DD — معاملات ذلك اليوم فقط (UTC حسب created_at المخزّن)
    search_q: نص يطابق اسم العميل أو الملاحظة أو المبلغ
    """
    from sqlalchemy import String, cast, or_

    db = SessionLocal()
    try:
        q = (
            db.query(CustomerTransaction, Customer)
            .join(Customer, Customer.id == CustomerTransaction.customer_id)
            .filter(Customer.user_id == user_id)
        )
        ds = (on_date or "").strip()[:10]
        if ds:
            try:
                day = datetime.strptime(ds, "%Y-%m-%d").date()
                start = datetime.combine(day, datetime.min.time())
                end = start + timedelta(days=1)
                q = q.filter(
                    CustomerTransaction.created_at >= start,
                    CustomerTransaction.created_at < end,
                )
            except ValueError:
                pass

        sq = (search_q or "").strip()
        if sq:
            like_pat = f"%{sq}%"
            q = q.filter(
                or_(
                    Customer.name.ilike(like_pat),
                    CustomerTransaction.note.ilike(like_pat),
                    cast(CustomerTransaction.amount, String).ilike(like_pat),
                )
            )

        to = (time_order or "new").lower()
        if to == "all":
            to = "new"
        amt = (amount_filter or "all").lower()
        if amt not in ("all", "high", "low"):
            amt = "all"

        if amt == "high":
            q = q.order_by(CustomerTransaction.amount.desc(), CustomerTransaction.id.desc())
        elif amt == "low":
            q = q.order_by(CustomerTransaction.amount.asc(), CustomerTransaction.id.asc())
        elif to == "old":
            q = q.order_by(CustomerTransaction.created_at.asc(), CustomerTransaction.id.asc())
        else:
            q = q.order_by(CustomerTransaction.created_at.desc(), CustomerTransaction.id.desc())

        chunk = q.offset(offset).limit(limit + 1).all()
        has_more = len(chunk) > limit
        return (chunk[:limit], has_more)
    finally:
        db.close()


def load_dashboard_aggregate_totals(user_id: int) -> tuple[float, float, float]:
    """إجمالي أعطيت / أخذت / النتيجة لجميع عملاء المستخدم."""
    from sqlalchemy import func

    db = SessionLocal()
    try:
        gave_sum = (
            db.query(func.coalesce(func.sum(CustomerTransaction.amount), 0))
            .join(Customer, Customer.id == CustomerTransaction.customer_id)
            .filter(Customer.user_id == user_id, CustomerTransaction.kind == "gave")
            .scalar()
        )
        took_sum = (
            db.query(func.coalesce(func.sum(CustomerTransaction.amount), 0))
            .join(Customer, Customer.id == CustomerTransaction.customer_id)
            .filter(Customer.user_id == user_id, CustomerTransaction.kind == "took")
            .scalar()
        )
        g = float(gave_sum or 0)
        t = float(took_sum or 0)
        return (g, t, g - t)
    finally:
        db.close()
