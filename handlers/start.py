# -*- coding: utf-8 -*-
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import SessionLocal
from app_models import User
from config import ADMIN_ID


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "مرحباً بك في دفتر الحسابات 📒\n\n"
        "يمكنك تسجيل حساب جديد أو الدخول إلى حسابك."
    )
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == update.effective_user.id).first()
        if user:
            text = (
                f"مرحباً مجدداً، {user.full_name or user.username or 'صديقي'} 👋\n\n"
                "استخدم القائمة أدناه لإدارة دفترك."
            )
            keyboard = [
                [InlineKeyboardButton("📊 دفتر الحسابات", callback_data="menu_ledger")],
                [InlineKeyboardButton("💰 الديون", callback_data="menu_debts")],
                [InlineKeyboardButton("👤 حسابي", callback_data="menu_profile")],
            ]
            if update.effective_user.id == ADMIN_ID:
                keyboard.append([InlineKeyboardButton("🔐 لوحة الأدمن", callback_data="admin_panel")])
        else:
            keyboard = [
                [InlineKeyboardButton("📝 إنشاء حساب", callback_data="auth_register")],
                [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="auth_login")],
            ]
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """القائمة الرئيسية للمستخدم المسجل"""
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("📊 دفتر الحسابات", callback_data="menu_ledger")],
        [InlineKeyboardButton("💰 الديون", callback_data="menu_debts")],
        [InlineKeyboardButton("👤 حسابي", callback_data="menu_profile")],
    ]
    if update.effective_user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("🔐 لوحة الأدمن", callback_data="admin_panel")])
    await query.edit_message_text(
        "القائمة الرئيسية 📒\n\nاختر ما تريد:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
