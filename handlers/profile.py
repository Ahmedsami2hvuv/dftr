# -*- coding: utf-8 -*-
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import SessionLocal
from models import User
from config import ADMIN_ID


def get_current_user(db, telegram_id: int):
    return db.query(User).filter(User.telegram_id == telegram_id).first()


async def menu_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("يجب تسجيل الدخول أولاً.")
            return
        uname = f"@{user.username}" if user.username else "—"
        text = (
            "حسابك 👤\n\n"
            f"الاسم: {user.full_name or '—'}\n"
            f"المستخدم: {uname}\n"
            f"الهاتف: {user.phone or '—'}\n"
        )
        text += f"تاريخ التسجيل: {user.created_at.strftime('%Y-%m-%d')}"
        keyboard = [[InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")]]
        if update.effective_user.id == ADMIN_ID:
            keyboard.insert(0, [InlineKeyboardButton("🔐 لوحة الأدمن", callback_data="admin_panel")])
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
