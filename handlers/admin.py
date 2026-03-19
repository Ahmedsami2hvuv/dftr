# -*- coding: utf-8 -*-
"""لوحة الأدمن"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import SessionLocal
from app_models import User, LedgerEntry, Debt
from config import ADMIN_ID


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إظهار لوحة الأدمن للأدمن فقط"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        if update.callback_query:
            await update.callback_query.answer("غير مسموح.")
        return
    query = update.callback_query
    if query:
        await query.answer()
    db = SessionLocal()
    try:
        users_count = db.query(User).count()
        entries_count = db.query(LedgerEntry).count()
        debts_count = db.query(Debt).count()
        text = (
            "لوحة الأدمن 🔐\n\n"
            f"عدد المستخدمين: {users_count}\n"
            f"عدد قيود الدفتر: {entries_count}\n"
            f"عدد سجلات الديون: {debts_count}\n"
        )
        keyboard = [
            [InlineKeyboardButton("👥 قائمة المستخدمين", callback_data="admin_users")],
            [InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")],
        ]
        if query:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()


async def admin_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    db = SessionLocal()
    try:
        users = db.query(User).order_by(User.created_at.desc()).limit(50).all()
        lines = ["المستخدمون (آخر 50):\n"]
        for u in users:
            lines.append(
                f"• {u.full_name or u.username or '—'} | {u.phone or '—'} | id:{u.telegram_id}"
            )
        keyboard = [[InlineKeyboardButton("◀ لوحة الأدمن", callback_data="admin_panel")]]
        await query.edit_message_text(
            "\n".join(lines) if len(lines) > 1 else "لا مستخدمين بعد.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
