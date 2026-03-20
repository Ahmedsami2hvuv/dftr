# -*- coding: utf-8 -*-
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import SessionLocal
from app_models import User
from config import ADMIN_ID


def get_current_user(db, telegram_id: int):
    return db.query(User).filter(User.telegram_id == telegram_id).first()


async def menu_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["last_menu"] = "profile"
    if context.user_data.get("force_login"):
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="auth_login")],
                [InlineKeyboardButton("📝 إنشاء حساب", callback_data="auth_register")],
                [InlineKeyboardButton("🔑 نسيت كلمة المرور", callback_data="auth_forgot")],
            ]
        )
        await query.edit_message_text("يجب تسجيل الدخول أولاً. استخدم الأزرار أدناه.", reply_markup=keyboard)
        return
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text(
                "يجب تسجيل الدخول أولاً.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="auth_login")],
                        [InlineKeyboardButton("📝 إنشاء حساب", callback_data="auth_register")],
                        [InlineKeyboardButton("🔑 نسيت كلمة المرور", callback_data="auth_forgot")],
                    ]
                ),
            )
            return
        uname = f"@{user.username}" if user.username else "—"
        text = (
            "حسابك 👤\n\n"
            f"الاسم: {user.full_name or '—'}\n"
            f"المستخدم: {uname}\n"
            f"الهاتف: {user.phone or '—'}\n"
        )
        text += f"تاريخ التسجيل: {user.created_at.strftime('%Y-%m-%d')}"
        keyboard = [
            [InlineKeyboardButton("🔐 تغيير الرمز", callback_data="auth_change_password")],
            [InlineKeyboardButton("💡 إرسال مشكلة أو اقتراح", callback_data="send_feedback")],
            [InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")],
        ]
        if update.effective_user.id == ADMIN_ID:
            keyboard.insert(0, [InlineKeyboardButton("🔐 لوحة الأدمن", callback_data="admin_panel")])
        keyboard.append([InlineKeyboardButton("🚪 تسجيل خروج", callback_data="auth_logout_confirm")])
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
