# -*- coding: utf-8 -*-
"""تسجيل، دخول، نسيت كلمة المرور"""
import logging
import random
import string
from datetime import datetime, timedelta
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import SessionLocal
from app_models import User
from config import ADMIN_ID
from utils.password import hash_password, check_password
from utils.phone import (
    is_plausible_iraq_mobile as _is_plausible_phone,
    normalize_phone as _normalize_phone,
    same_phone as _same_phone,
    wa_number as _wa_number,
)

logger = logging.getLogger(__name__)

(
    REG_NAME,
    REG_PHONE,
    REG_PASSWORD,
    LOGIN_PHONE,
    LOGIN_PASSWORD,
    FORGOT_PHONE,
    FORGOT_WAIT,
    FORGOT_CODE,
    FORGOT_NEW_PASSWORD,
    FORGOT_NEW_PASSWORD_CONFIRM,
    CHPWD_OLD,
    CHPWD_NEW,
    CHPWD_NEW_CONFIRM,
) = range(13)


def _kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")]]
    )


def get_user_by_telegram(db, telegram_id: int):
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def _find_user_by_phone(db, phone_normalized: str):
    """يطابق الرقم المخزّن حتى لو كان محفوظاً بصيغة قديمة (بدون +964، مسافات، …)."""
    u = db.query(User).filter(User.phone == phone_normalized).first()
    if u:
        return u
    for u in db.query(User).filter(User.phone.isnot(None)):
        if _same_phone(u.phone, phone_normalized):
            return u
    return None


def _clear_quick_amount_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """تفريغ مسار المبلغ السريع حتى لا يتداخل مع مسارات المصادقة."""
    context.user_data.pop("quick_amount", None)
    context.user_data.pop("quick_flow_kind", None)




# --- التسجيل ---
async def auth_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["auth_action"] = "register"
    context.user_data.pop("force_login", None)
    _clear_quick_amount_state(context)
    await query.edit_message_text(
        "إنشاء حساب جديد 📝\n\nأرسل اسمك الكامل (أو ما تريد أن يظهر في الدفتر):"
    )
    return REG_NAME


async def reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("يرجى إرسال اسم صحيح.", reply_markup=_kb_main_menu())
        return REG_NAME
    context.user_data["reg_name"] = name
    await update.message.reply_text(
        "تم حفظ الاسم ✅\n\nأرسل رقم هاتفك (مثال: +9647733921468):"
    )
    return REG_PHONE


async def reg_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.contact:
        phone_raw = update.message.contact.phone_number or ""
    else:
        phone_raw = update.message.text or ""
    phone = _normalize_phone(phone_raw)
    if not phone or not _is_plausible_phone(phone):
        await update.message.reply_text(
            "يرجى إرسال رقم هاتف صحيح (مثلاً 077… أو 7××× أو +964…).",
            reply_markup=_kb_main_menu(),
        )
        return REG_PHONE
    db = SessionLocal()
    try:
        if _find_user_by_phone(db, phone):
            await update.message.reply_text(
                "هذا الرقم مسجل مسبقاً. استخدم «تسجيل الدخول» أو رقم آخر.",
                reply_markup=_kb_main_menu(),
            )
            return REG_PHONE
        context.user_data["reg_phone"] = phone
        await update.message.reply_text(
            "تم ✅\n\nأرسل كلمة المرور التي تريد استخدامها للدخول (احفظها):"
        )
        return REG_PASSWORD
    finally:
        db.close()


async def reg_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = (update.message.text or "").strip()
    if not password or len(password) < 4:
        await update.message.reply_text(
            "كلمة المرور يجب أن تكون 4 أحرف على الأقل.",
            reply_markup=_kb_main_menu(),
        )
        return REG_PASSWORD
    db = SessionLocal()
    try:
        user = User(
            telegram_id=update.effective_user.id,
            username=update.effective_user.username,
            full_name=context.user_data.get("reg_name"),
            phone=context.user_data.get("reg_phone"),
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        pending_token = context.user_data.get("pending_partner_invite_token")
        if pending_token:
            from handlers.partner_link import handle_start_partner_link

            # لا نعرض القائمة الرئيسية قبل شاشة الربط إذا كان هناك توكن دعوة معلّق.
            await handle_start_partner_link(update, context, pending_token)
            context.user_data.pop("pending_partner_invite_token", None)
        else:
            keyboard = [[InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")]]
            await update.message.reply_text(
                "تم إنشاء حسابك بنجاح ✅\n\nاستخدم القائمة لإدارة دفترك.",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    finally:
        db.close()
    for k in ("reg_name", "reg_phone", "auth_action"):
        context.user_data.pop(k, None)
    return ConversationHandler.END


# --- تسجيل الدخول ---
async def auth_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["auth_action"] = "login"
    context.user_data.pop("force_login", None)
    _clear_quick_amount_state(context)
    await query.edit_message_text(
        "تسجيل الدخول 🔐\n\nأرسل رقم هاتفك المسجل (مثال: +9647733921468):"
    )
    return LOGIN_PHONE


async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.contact:
        phone_raw = update.message.contact.phone_number or ""
    else:
        phone_raw = update.message.text or ""
    phone = _normalize_phone(phone_raw)
    if not phone or not _is_plausible_phone(phone):
        await update.message.reply_text(
            "يرجى إرسال رقم هاتف صحيح كما سجّلته.",
            reply_markup=_kb_main_menu(),
        )
        return LOGIN_PHONE
    context.user_data["login_phone"] = phone
    await update.message.reply_text("أرسل كلمة المرور:", reply_markup=_kb_main_menu())
    return LOGIN_PASSWORD


async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = (update.message.text or "").strip()
    phone = context.user_data.get("login_phone")
    db = SessionLocal()
    try:
        try:
            user = _find_user_by_phone(db, phone)
            if not user:
                await update.message.reply_text(
                    "لا يوجد حساب بهذا الرقم. أنشئ حساباً جديداً.",
                    reply_markup=_kb_main_menu(),
                )
                context.user_data.pop("login_phone", None)
                return ConversationHandler.END
            if user.phone != phone:
                user.phone = phone
            if user.password_hash and not check_password(password, user.password_hash):
                await update.message.reply_text(
                    "كلمة المرور غير صحيحة.",
                    reply_markup=_kb_main_menu(),
                )
                return LOGIN_PASSWORD
            if not user.password_hash:
                user.password_hash = hash_password(password)
                db.commit()
            if user.telegram_id and user.telegram_id != update.effective_user.id:
                await update.message.reply_text(
                    "هذا الحساب مربوط بحساب تليجرام آخر. تواصل مع الإدارة إن كان خطأ."
                )
                context.user_data.pop("login_phone", None)
                return ConversationHandler.END
            user.telegram_id = update.effective_user.id
            user.username = update.effective_user.username
            db.commit()
            pending_token = context.user_data.get("pending_partner_invite_token")
            if pending_token:
                from handlers.partner_link import handle_start_partner_link

                await handle_start_partner_link(update, context, pending_token)
                context.user_data.pop("pending_partner_invite_token", None)
            else:
                keyboard = [[InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")]]
                await update.message.reply_text(
                    f"تم تسجيل الدخول بنجاح ✅ مرحباً {user.full_name or user.username or 'بك'}.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
        except Exception:
            await update.message.reply_text(
                "حدث خطأ أثناء تسجيل الدخول. حاول مرة أخرى أو استخدم /start.",
                reply_markup=_kb_main_menu(),
            )
            return ConversationHandler.END
    finally:
        db.close()
    context.user_data.pop("login_phone", None)
    context.user_data.pop("auth_action", None)
    return ConversationHandler.END


# --- نسيت كلمة المرور ---
async def auth_forgot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["auth_action"] = "forgot"
    context.user_data.pop("force_login", None)
    _clear_quick_amount_state(context)
    await query.edit_message_text(
        "نسيت كلمة المرور 🔑\n\nأرسل رقم هاتفك المسجل في الحساب:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")]]),
    )
    return FORGOT_PHONE


async def auth_forgot_start_deeplink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رابط الموقع t.me/bot?start=forgot — يبدأ نفس محادثة نسيت كلمة المرور."""
    context.user_data["auth_action"] = "forgot"
    context.user_data.pop("force_login", None)
    _clear_quick_amount_state(context)
    await update.message.reply_text(
        "نسيت كلمة المرور 🔑\n\nأرسل رقم هاتفك المسجل في الحساب:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")]]),
    )
    return FORGOT_PHONE


async def forgot_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.contact:
        phone_raw = update.message.contact.phone_number or ""
    else:
        phone_raw = update.message.text or ""
    phone = _normalize_phone(phone_raw)
    if not phone or not _is_plausible_phone(phone):
        await update.message.reply_text(
            "يرجى إرسال رقم هاتف صحيح كما في الحساب.",
            reply_markup=_kb_main_menu(),
        )
        return FORGOT_PHONE
    db = SessionLocal()
    try:
        user = _find_user_by_phone(db, phone)
        if not user:
            await update.message.reply_text(
                "لا يوجد حساب مسجل بهذا الرقم.",
                reply_markup=_kb_main_menu(),
            )
            return FORGOT_PHONE
        code = "".join(random.choices(string.digits, k=6))
        user.reset_code = code
        user.reset_code_expires = datetime.utcnow() + timedelta(minutes=15)
        db.commit()
        context.user_data["forgot_phone"] = phone

        # إشعار الأدمن: شخص نسي الرمز + زر واتساب
        if ADMIN_ID:
            wa_num = _wa_number(phone)
            wa_text = f"رمزك في بوت دفتر الديون هو: {code}"
            wa_url = f"https://wa.me/{wa_num}?text={quote(wa_text)}"
            admin_keyboard = [
                [InlineKeyboardButton("فتح واتساب وإرسال الرمز", url=wa_url)],
            ]
            await context.bot.send_message(
                ADMIN_ID,
                f"⚠️ شخص نسي كلمة المرور.\nرقم الهاتف: {phone}\nتحقق وأرسل له الرمز عبر واتساب ثم اضغط الزر أدناه لفتح المحادثة.",
                reply_markup=InlineKeyboardMarkup(admin_keyboard),
            )

        keyboard = [
            [InlineKeyboardButton("بعد استلام الرمز انقر هنا", callback_data="forgot_enter_code")],
            [InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")],
        ]
        await update.message.reply_text(
            "تم التحقق من رقمك ✅\n\nسنرسل لك الرمز عبر واتساب. عند وصول الرسالة انقر على الزر الظاهر أسفل هذه الرسالة ثم أدخل الرمز.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return FORGOT_WAIT
    finally:
        db.close()


async def forgot_enter_code_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [
            InlineKeyboardButton("↩ رجوع", callback_data="forgot_back_phone"),
        ],
        [InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")],
    ]
    await query.edit_message_text(
        "أرسل الرمز المرسل لك عبر الواتساب:\n\n"
        "الرجاء كتابة الرمز بعد وصوله عبر واتساب، أو رجوع لإدخال رقم الهاتف مرة أخرى.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return FORGOT_CODE


async def forgot_back_phone_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # إرجاع المستخدم لإدخال رقم الهاتف من جديد
    context.user_data.pop("forgot_phone", None)
    await query.edit_message_text(
        "أرسل رقم هاتفك المسجل في الحساب:",
        reply_markup=_kb_main_menu(),
    )
    return FORGOT_PHONE


async def forgot_copy_code_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    phone = context.user_data.get("forgot_phone")
    if not phone:
        await query.edit_message_text(
            "انتهت الجلسة. ابدأ من «نسيت كلمة المرور» مرة أخرى.",
            reply_markup=_kb_main_menu(),
        )
        return FORGOT_PHONE
    db = SessionLocal()
    try:
        user = _find_user_by_phone(db, phone)
        if not user or not user.reset_code:
            await query.edit_message_text(
                "لم يتم العثور على رمز. استخدم «نسيت كلمة المرور» مرة أخرى.",
                reply_markup=_kb_main_menu(),
            )
            return ConversationHandler.END
        if user.reset_code_expires and user.reset_code_expires < datetime.utcnow():
            await query.edit_message_text(
                "انتهت صلاحية الرمز. استخدم «نسيت كلمة المرور» مرة أخرى.",
                reply_markup=_kb_main_menu(),
            )
            return ConversationHandler.END
        code = user.reset_code
        # عرض الرمز داخل تليجرام (ليتم نسخه ولصقه)
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=f"رمزك هو: {code}\n\nانسخه والصقه في رسالة الرمز.",
        )
        # ارجع لنفس رسالة إدخال الرمز
        keyboard = [
            [
                InlineKeyboardButton("↩ رجوع", callback_data="forgot_back_phone"),
                InlineKeyboardButton("📋 نسخ الرمز", callback_data="forgot_copy_code"),
            ]
        ]
        await query.edit_message_text("أرسل الرمز المرسل لك عبر الواتساب:", reply_markup=InlineKeyboardMarkup(keyboard))
        return FORGOT_CODE
    finally:
        db.close()


async def forgot_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = (update.message.text or "").strip()
    phone = context.user_data.get("forgot_phone")
    if not phone:
        await update.message.reply_text(
            "انتهت الجلسة. ابدأ من «نسيت كلمة المرور» مرة أخرى.",
            reply_markup=_kb_main_menu(),
        )
        return ConversationHandler.END
    db = SessionLocal()
    try:
        user = _find_user_by_phone(db, phone)
        if not user:
            await update.message.reply_text(
                "لا يوجد حساب بهذا الرقم.",
                reply_markup=_kb_main_menu(),
            )
            context.user_data.pop("forgot_phone", None)
            return ConversationHandler.END
        if not user.reset_code or user.reset_code != code:
            await update.message.reply_text(
                "الرمز غير صحيح. تحقق من الرمز وأعد المحاولة.",
                reply_markup=_kb_main_menu(),
            )
            return FORGOT_CODE
        if user.reset_code_expires and user.reset_code_expires < datetime.utcnow():
            await update.message.reply_text(
                "انتهت صلاحية الرمز. استخدم «نسيت كلمة المرور» مرة أخرى.",
                reply_markup=_kb_main_menu(),
            )
            user.reset_code = None
            user.reset_code_expires = None
            db.commit()
            context.user_data.pop("forgot_phone", None)
            return ConversationHandler.END
        # التحقق ناجح: لا نربط التليجرام بعد — يجب تعيين كلمة مرور جديدة أولاً
        user.reset_code = None
        user.reset_code_expires = None
        db.commit()
        context.user_data["forgot_reset_user_id"] = user.id
        await update.message.reply_text(
            "تم التحقق من الرمز ✅\n\n"
            "لم يُكمل النظام تسجيل الدخول بعد؛ يجب أن تضع الآن كلمة مرور جديدة للحساب (4 أحرف على الأقل).\n\n"
            "أرسل كلمة المرور الجديدة:",
        )
    finally:
        db.close()
    return FORGOT_NEW_PASSWORD


async def forgot_new_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pwd = (update.message.text or "").strip()
    uid = context.user_data.get("forgot_reset_user_id")
    if not uid:
        await update.message.reply_text(
            "انتهت الجلسة. ابدأ من «نسيت كلمة المرور» مرة أخرى.",
            reply_markup=_kb_main_menu(),
        )
        return ConversationHandler.END
    if not pwd or len(pwd) < 4:
        await update.message.reply_text(
            "كلمة المرور يجب أن تكون 4 أحرف على الأقل.",
            reply_markup=_kb_main_menu(),
        )
        return FORGOT_NEW_PASSWORD
    context.user_data["forgot_new_pwd"] = pwd
    await update.message.reply_text(
        "أعد إدخال كلمة المرور الجديدة للتأكيد:",
    )
    return FORGOT_NEW_PASSWORD_CONFIRM


async def forgot_new_password_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pwd2 = (update.message.text or "").strip()
    uid = context.user_data.get("forgot_reset_user_id")
    pwd1 = context.user_data.get("forgot_new_pwd")
    if not uid or not pwd1:
        await update.message.reply_text(
            "انتهت الجلسة. ابدأ من «نسيت كلمة المرور» مرة أخرى.",
            reply_markup=_kb_main_menu(),
        )
        return ConversationHandler.END
    if pwd2 != pwd1:
        await update.message.reply_text(
            "التأكيد لا يطابق كلمة المرور. أرسل كلمة مرور جديدة مرة أخرى:",
            reply_markup=_kb_main_menu(),
        )
        context.user_data.pop("forgot_new_pwd", None)
        return FORGOT_NEW_PASSWORD
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == uid).first()
        if not user:
            await update.message.reply_text(
                "خطأ في الحساب. أعد المحاولة.",
                reply_markup=_kb_main_menu(),
            )
            return ConversationHandler.END
        if user.telegram_id and user.telegram_id != update.effective_user.id:
            await update.message.reply_text(
                "هذا الحساب مربوط بتليجرام آخر. تواصل مع الإدارة إن كان خطأ.",
                reply_markup=_kb_main_menu(),
            )
            return ConversationHandler.END
        user.password_hash = hash_password(pwd1)
        user.telegram_id = update.effective_user.id
        user.username = update.effective_user.username
        db.commit()
        pending_token = context.user_data.get("pending_partner_invite_token")
        if pending_token:
            from handlers.partner_link import handle_start_partner_link

            await handle_start_partner_link(update, context, pending_token)
            context.user_data.pop("pending_partner_invite_token", None)
        else:
            keyboard = [[InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")]]
            await update.message.reply_text(
                f"تم تعيين كلمة المرور وتسجيل الدخول ✅ مرحباً {user.full_name or user.username or 'بك'}.",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    finally:
        db.close()
    for k in (
        "forgot_reset_user_id",
        "forgot_new_pwd",
        "forgot_phone",
        "auth_action",
    ):
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def cancel_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for k in (
        "reg_name",
        "reg_phone",
        "auth_action",
        "login_phone",
        "forgot_phone",
        "forgot_reset_user_id",
        "forgot_new_pwd",
        "pending_partner_invite_token",
        "chpwd_old_ok",
        "chpwd_new",
    ):
        context.user_data.pop(k, None)
    context.user_data.pop("in_cust_cat_add_flow", None)
    await update.message.reply_text("تم الرجوع.", reply_markup=_kb_main_menu())
    return ConversationHandler.END


async def auth_change_password_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        u = get_user_by_telegram(db, update.effective_user.id)
        if not u:
            await query.edit_message_text(
                "يجب تسجيل الدخول أولاً.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="auth_login")],
                        [InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")],
                    ]
                ),
            )
            return ConversationHandler.END
    finally:
        db.close()
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔑 نسيت الرمز", callback_data="chpwd_use_forgot"),
            ],
            [InlineKeyboardButton("◀ حسابي", callback_data="menu_profile")],
        ]
    )
    await query.edit_message_text(
        "🔐 تغيير كلمة المرور\n\nأرسل كلمة المرور الحالية:",
        reply_markup=kb,
    )
    return CHPWD_OLD


async def chpwd_use_forgot_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "للاستعادة بدون كلمة المرور الحالية اختر «نسيت كلمة المرور» أدناه."
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔑 نسيت كلمة المرور", callback_data="auth_forgot")],
            [InlineKeyboardButton("◀ حسابي", callback_data="menu_profile")],
        ]
    )
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except Exception:
        # أحياناً قد تفشل edit على رسائل قديمة؛ نضمن ظهور النتيجة برسالة جديدة.
        await context.bot.send_message(chat_id=update.effective_user.id, text=text, reply_markup=kb)
    return ConversationHandler.END


async def chpwd_old(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pwd = (update.message.text or "").strip()
    db = SessionLocal()
    try:
        user = get_user_by_telegram(db, update.effective_user.id)
        if not user:
            await update.message.reply_text("انتهت الجلسة.", reply_markup=_kb_main_menu())
            return ConversationHandler.END
        if user.password_hash and not check_password(pwd, user.password_hash):
            await update.message.reply_text(
                "كلمة المرور الحالية غير صحيحة. حاول مرة أخرى أو استخدم «نسيت الرمز» من شاشة تغيير المرور.",
                reply_markup=_kb_main_menu(),
            )
            return CHPWD_OLD
        if not user.password_hash:
            await update.message.reply_text(
                "لا توجد كلمة مرور مخزنة لهذا الحساب. استخدم تسجيل الدخول أو نسيت كلمة المرور.",
                reply_markup=_kb_main_menu(),
            )
            return ConversationHandler.END
    finally:
        db.close()
    context.user_data["chpwd_old_ok"] = True
    await update.message.reply_text(
        "أرسل كلمة المرور الجديدة (4 أحرف على الأقل):",
        reply_markup=_kb_main_menu(),
    )
    return CHPWD_NEW


async def chpwd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("chpwd_old_ok"):
        await update.message.reply_text("ابدأ من «تغيير الرمز» في حسابي.", reply_markup=_kb_main_menu())
        return ConversationHandler.END
    pwd = (update.message.text or "").strip()
    if not pwd or len(pwd) < 4:
        await update.message.reply_text(
            "كلمة المرور يجب أن تكون 4 أحرف على الأقل.",
            reply_markup=_kb_main_menu(),
        )
        return CHPWD_NEW
    context.user_data["chpwd_new"] = pwd
    await update.message.reply_text("أعد إدخال كلمة المرور الجديدة للتأكيد:", reply_markup=_kb_main_menu())
    return CHPWD_NEW_CONFIRM


async def chpwd_new_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pwd2 = (update.message.text or "").strip()
    pwd1 = context.user_data.get("chpwd_new")
    if not context.user_data.get("chpwd_old_ok") or not pwd1:
        await update.message.reply_text("انتهت الجلسة. افتح «تغيير الرمز» من جديد.", reply_markup=_kb_main_menu())
        return ConversationHandler.END
    if pwd2 != pwd1:
        await update.message.reply_text(
            "التأكيد لا يطابق. أرسل كلمة مرور جديدة مرة أخرى:",
            reply_markup=_kb_main_menu(),
        )
        context.user_data.pop("chpwd_new", None)
        return CHPWD_NEW
    db = SessionLocal()
    try:
        user = get_user_by_telegram(db, update.effective_user.id)
        if not user:
            await update.message.reply_text("انتهت الجلسة.", reply_markup=_kb_main_menu())
            return ConversationHandler.END
        user.password_hash = hash_password(pwd1)
        db.commit()
        await update.message.reply_text(
            "تم تغيير كلمة المرور ✅",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀ حسابي", callback_data="menu_profile")]]
            ),
        )
    finally:
        db.close()
    for k in ("chpwd_old_ok", "chpwd_new"):
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def auth_logout_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        u = get_user_by_telegram(db, update.effective_user.id)
        if not u:
            await query.edit_message_text(
                "أنت غير مسجل حالياً.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="auth_login")],
                        [InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")],
                    ]
                ),
            )
            return
    finally:
        db.close()
    await query.edit_message_text(
        "هل تريد حقاً تسجيل الخروج من هذا الحساب على هذا الجهاز؟",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ نعم، تسجيل الخروج", callback_data="auth_logout_do")],
                [InlineKeyboardButton("◀ رجوع", callback_data="menu_profile")],
            ]
        ),
    )


async def auth_logout_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل خروج حقيقي: إزالة ربط telegram_id حتى يرجع يطلب تسجيل الدخول."""
    query = update.callback_query
    if not update.effective_user:
        return ConversationHandler.END
    tid = int(update.effective_user.id)
    if query:
        try:
            await query.answer("جاري تسجيل الخروج...")
        except Exception:
            pass

    db = SessionLocal()
    try:
        keyboard = [
            [InlineKeyboardButton("📝 إنشاء حساب", callback_data="auth_register")],
            [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="auth_login")],
            [InlineKeyboardButton("🔑 نسيت كلمة المرور", callback_data="auth_forgot")],
        ]

        affected = (
            db.query(User)
            .filter(User.telegram_id == tid)
            .update({User.telegram_id: None, User.username: None}, synchronize_session=False)
        )
        db.commit()
        logger.info("تسجيل خروج: telegram_id=%s affected=%s", tid, affected)

        out_text = (
            "تم تسجيل الخروج ✅\n\nللوصول إلى حسابك مرة أخرى استخدم تسجيل الدخول أو إنشاء حساب أو نسيت كلمة المرور."
            if affected > 0
            else "أنت غير مسجل حالياً. اختر خيار تسجيل الدخول."
        )
    except Exception as e:
        db.rollback()
        logger.exception("فشل تسجيل الخروج telegram_id=%s: %s", tid, e)
        out_text = "تعذر إكمال تسجيل الخروج حالياً. حاول مرة أخرى."
    finally:
        db.close()

    # منع أي "زر قديم" في نفس جهاز المستخدم من إعادة فتح الحساب مباشرة بعد تسجيل الخروج.
    context.user_data["force_login"] = True
    context.user_data.clear()
    context.user_data["force_login"] = True

    # رسالة واحدة واضحة؛ وإذا تعذر تعديل الرسالة نرسل رسالة جديدة.
    if query:
        try:
            await query.edit_message_text(out_text, reply_markup=InlineKeyboardMarkup(keyboard))
            return ConversationHandler.END
        except Exception:
            pass
    await context.bot.send_message(
        chat_id=tid,
        text=out_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    return ConversationHandler.END
