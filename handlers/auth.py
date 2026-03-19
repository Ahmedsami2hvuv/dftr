# -*- coding: utf-8 -*-
"""تسجيل، دخول، نسيت كلمة المرور"""
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
from utils.phone import normalize_phone as _normalize_phone, wa_number as _wa_number

(
    REG_NAME,
    REG_PHONE,
    REG_PASSWORD,
    LOGIN_PHONE,
    LOGIN_PASSWORD,
    FORGOT_PHONE,
    FORGOT_WAIT,
    FORGOT_CODE,
) = range(8)


def get_user_by_telegram(db, telegram_id: int):
    return db.query(User).filter(User.telegram_id == telegram_id).first()




# --- التسجيل ---
async def auth_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["auth_action"] = "register"
    await query.edit_message_text(
        "إنشاء حساب جديد 📝\n\nأرسل اسمك الكامل (أو ما تريد أن يظهر في الدفتر):"
    )
    return REG_NAME


async def reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("يرجى إرسال اسم صحيح.")
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
    if not phone or len(phone) < 10:
        await update.message.reply_text("يرجى إرسال رقم هاتف صحيح.")
        return REG_PHONE
    db = SessionLocal()
    try:
        if db.query(User).filter(User.phone == phone).first():
            await update.message.reply_text(
                "هذا الرقم مسجل مسبقاً. استخدم «تسجيل الدخول» أو رقم آخر."
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
        await update.message.reply_text("كلمة المرور يجب أن تكون 4 أحرف على الأقل.")
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
    if not phone:
        await update.message.reply_text("يرجى إرسال رقمك المسجل.")
        return LOGIN_PHONE
    context.user_data["login_phone"] = phone
    await update.message.reply_text("أرسل كلمة المرور:")
    return LOGIN_PASSWORD


async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = (update.message.text or "").strip()
    phone = context.user_data.get("login_phone")
    db = SessionLocal()
    try:
        try:
            user = db.query(User).filter(User.phone == phone).first()
            if not user:
                await update.message.reply_text("لا يوجد حساب بهذا الرقم. أنشئ حساباً جديداً.")
                context.user_data.pop("login_phone", None)
                return ConversationHandler.END
            if user.password_hash and not check_password(password, user.password_hash):
                await update.message.reply_text("كلمة المرور غير صحيحة.")
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
            keyboard = [[InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")]]
            await update.message.reply_text(
                f"تم تسجيل الدخول بنجاح ✅ مرحباً {user.full_name or user.username or 'بك'}.",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            await update.message.reply_text(
                "حدث خطأ أثناء تسجيل الدخول. حاول مرة أخرى أو استخدم /start."
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
    await query.edit_message_text(
        "نسيت كلمة المرور 🔑\n\nأرسل رقم هاتفك المسجل في الحساب:"
    )
    return FORGOT_PHONE


async def forgot_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.contact:
        phone_raw = update.message.contact.phone_number or ""
    else:
        phone_raw = update.message.text or ""
    phone = _normalize_phone(phone_raw)
    if not phone:
        await update.message.reply_text("يرجى إرسال رقم الهاتف.")
        return FORGOT_PHONE
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.phone == phone).first()
        if not user:
            await update.message.reply_text("لا يوجد حساب مسجل بهذا الرقم.")
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
            InlineKeyboardButton("📋 نسخ الرمز", callback_data="forgot_copy_code"),
        ]
    ]
    await query.edit_message_text(
        "أرسل الرمز المرسل لك عبر الواتساب:\n\n"
        "يمكنك نسخ الرمز من الزر أو الرجوع لإدخال رقم الهاتف مرة أخرى.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return FORGOT_CODE


async def forgot_back_phone_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # إرجاع المستخدم لإدخال رقم الهاتف من جديد
    context.user_data.pop("forgot_phone", None)
    await query.edit_message_text("أرسل رقم هاتفك المسجل في الحساب:")
    return FORGOT_PHONE


async def forgot_copy_code_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    phone = context.user_data.get("forgot_phone")
    if not phone:
        await query.edit_message_text("انتهت الجلسة. ابدأ من «نسيت كلمة المرور» مرة أخرى.")
        return FORGOT_PHONE
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.phone == phone).first()
        if not user or not user.reset_code:
            await query.edit_message_text("لم يتم العثور على رمز. استخدم «نسيت كلمة المرور» مرة أخرى.")
            return ConversationHandler.END
        if user.reset_code_expires and user.reset_code_expires < datetime.utcnow():
            await query.edit_message_text("انتهت صلاحية الرمز. استخدم «نسيت كلمة المرور» مرة أخرى.")
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
        await update.message.reply_text("انتهت الجلسة. ابدأ من «نسيت كلمة المرور» مرة أخرى.")
        return ConversationHandler.END
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.phone == phone).first()
        if not user:
            await update.message.reply_text("لا يوجد حساب بهذا الرقم.")
            context.user_data.pop("forgot_phone", None)
            return ConversationHandler.END
        if not user.reset_code or user.reset_code != code:
            await update.message.reply_text("الرمز غير صحيح. تحقق من الرمز وأعد المحاولة.")
            return FORGOT_CODE
        if user.reset_code_expires and user.reset_code_expires < datetime.utcnow():
            await update.message.reply_text("انتهت صلاحية الرمز. استخدم «نسيت كلمة المرور» مرة أخرى.")
            user.reset_code = None
            user.reset_code_expires = None
            db.commit()
            context.user_data.pop("forgot_phone", None)
            return ConversationHandler.END
        user.telegram_id = update.effective_user.id
        user.username = update.effective_user.username
        user.reset_code = None
        user.reset_code_expires = None
        db.commit()
        keyboard = [[InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")]]
        await update.message.reply_text(
            f"تم التحقق بنجاح ✅ مرحباً {user.full_name or 'بك'}.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
    context.user_data.pop("forgot_phone", None)
    context.user_data.pop("auth_action", None)
    return ConversationHandler.END


async def cancel_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for k in ("reg_name", "reg_phone", "auth_action", "login_phone", "forgot_phone"):
        context.user_data.pop(k, None)
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END


async def auth_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل خروج حقيقي: إزالة ربط telegram_id حتى يرجع يطلب تسجيل الدخول."""
    query = update.callback_query
    chat_id = None
    if query and query.from_user:
        chat_id = query.from_user.id
    elif update.effective_user:
        chat_id = update.effective_user.id
    if query:
        try:
            await query.answer("جاري تسجيل الخروج...")
        except Exception:
            pass

    db = SessionLocal()
    try:
        if not chat_id:
            return ConversationHandler.END
        keyboard = [
            [InlineKeyboardButton("📝 إنشاء حساب", callback_data="auth_register")],
            [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="auth_login")],
            [InlineKeyboardButton("🔑 نسيت كلمة المرور", callback_data="auth_forgot")],
        ]

        # تحديث مباشر أكثر ثباتاً لتفريغ الربط الحالي
        try:
            affected = (
                db.query(User)
                .filter(User.telegram_id == chat_id)
                .update({User.telegram_id: None, User.username: None}, synchronize_session=False)
            )
            db.commit()
        except Exception:
            db.rollback()
            affected = 0

        out_text = (
            "تم تسجيل الخروج ✅\n\nالآن يجب تسجيل الدخول مرة أخرى."
            if affected > 0
            else "أنت غير مسجل حالياً. اختر خيار تسجيل الدخول."
        )

        # أرسل في نفس المحادثة الحالية لثبات أعلى
        if query and query.message:
            await query.message.reply_text(out_text, reply_markup=InlineKeyboardMarkup(keyboard))
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=out_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

        for k in ("reg_name", "reg_phone", "auth_action", "login_phone", "forgot_phone"):
            context.user_data.pop(k, None)
    finally:
        db.close()

    return ConversationHandler.END
