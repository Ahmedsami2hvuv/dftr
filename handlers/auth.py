# -*- coding: utf-8 -*-
"""تسجيل الدخول وإنشاء حساب"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import SessionLocal
from models import User

(
    AUTH_CHOOSE,
    REG_NAME,
    REG_PHONE,
    LOGIN_PHONE,
    LOGIN_CONFIRM,
) = range(5)


def get_user_by_telegram(db, telegram_id: int):
    return db.query(User).filter(User.telegram_id == telegram_id).first()


async def auth_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["auth_action"] = "register"
    await query.edit_message_text(
        "إنشاء حساب جديد 📝\n\nأرسل اسمك الكامل (أو ما تريد أن يظهر في الدفتر):"
    )
    return REG_NAME


async def auth_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["auth_action"] = "login"
    await query.edit_message_text(
        "تسجيل الدخول 🔐\n\nأرسل رقم هاتفك المسجل (مثال: +9647733921468):"
    )
    return LOGIN_PHONE


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
    phone = (update.message.text or "").strip()
    if not phone:
        await update.message.reply_text("يرجى إرسال رقم صحيح.")
        return REG_PHONE
    db = SessionLocal()
    try:
        if db.query(User).filter(User.phone == phone).first():
            await update.message.reply_text(
                "هذا الرقم مسجل مسبقاً. استخدم «تسجيل الدخول» أو رقم آخر."
            )
            return REG_PHONE
        user = User(
            telegram_id=update.effective_user.id,
            username=update.effective_user.username,
            full_name=context.user_data.get("reg_name"),
            phone=phone,
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
    context.user_data.pop("reg_name", None)
    context.user_data.pop("auth_action", None)
    return ConversationHandler.END


async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = (update.message.text or "").strip()
    if not phone:
        await update.message.reply_text("يرجى إرسال رقمك المسجل.")
        return LOGIN_PHONE
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.phone == phone).first()
        if not user:
            await update.message.reply_text(
                "لا يوجد حساب بهذا الرقم. أنشئ حساباً جديداً أولاً."
            )
            return LOGIN_PHONE
        if user.telegram_id and user.telegram_id != update.effective_user.id:
            await update.message.reply_text(
                "هذا الحساب مربوط بحساب تليجرام آخر. تواصل مع الإدارة إن كان خطأ."
            )
            return ConversationHandler.END
        user.telegram_id = update.effective_user.id
        user.username = update.effective_user.username
        db.commit()
        keyboard = [[InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")]]
        await update.message.reply_text(
            f"تم تسجيل الدخول بنجاح ✅ مرحباً {user.full_name or user.username or 'بك'}.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
    context.user_data.pop("auth_action", None)
    return ConversationHandler.END


async def cancel_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("reg_name", None)
    context.user_data.pop("auth_action", None)
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END
