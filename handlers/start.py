# -*- coding: utf-8 -*-
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import SessionLocal
from app_models import User, ShareLink
from config import ADMIN_ID


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    # رابط مشاركة: /start view_TOKEN
    if args and args[0].startswith("view_"):
        token = args[0].replace("view_", "", 1)
        db = SessionLocal()
        try:
            link = db.query(ShareLink).filter(ShareLink.token == token).first()
            if not link or (link.expires_at and link.expires_at < datetime.utcnow()):
                await update.message.reply_text("رابط غير صالح أو منتهي الصلاحية.")
                return
            cust = link.customer
            gave = sum(t.amount for t in cust.transactions if t.kind == "gave")
            took = sum(t.amount for t in cust.transactions if t.kind == "took")
            bal = float(gave - took)
            cur = "د.ع."
            lines = [f"معاملات مرتبطة برقم العميل: {cust.name}", f"الرصيد: {bal:.2f} {cur}\n"]
            for t in cust.transactions[:50]:
                kind_ar = "أعطيت" if t.kind == "gave" else "أخذت"
                lines.append(f"{t.created_at.strftime('%Y-%m-%d %H:%M')} — {kind_ar}: {t.amount} {cur}" + (f" — {t.note}" if t.note else ""))
            if len(cust.transactions) > 50:
                lines.append(f"\n... و {len(cust.transactions) - 50} معاملة أخرى")
            await update.message.reply_text("\n".join(lines))
        finally:
            db.close()
        return

    text = (
        "مرحباً بك في دفتر الحسابات 📒\n\n"
        "يمكنك تسجيل حساب جديد أو الدخول إلى حسابك."
    )
    db = SessionLocal()
    try:
        tid = int(update.effective_user.id)
        user = db.query(User).filter(User.telegram_id == tid).first()
        if user:
            text = (
                f"مرحباً مجدداً، {user.full_name or user.username or 'صديقي'} 👋\n\n"
                "استخدم القائمة أدناه لإدارة دفترك."
            )
            keyboard = [
                [InlineKeyboardButton("📒 دفتر الديون", callback_data="menu_customers")],
                [InlineKeyboardButton("📒 الدخل والمصروف", callback_data="menu_ledger")],
                [InlineKeyboardButton("👤 حسابي", callback_data="menu_profile")],
            ]
            if update.effective_user.id == ADMIN_ID:
                keyboard.append([InlineKeyboardButton("🔐 لوحة الأدمن", callback_data="admin_panel")])
        else:
            # غير مربوط بالحساب: صفّر أي حالة محادثة عالقة (مثلاً بعد تسجيل خروج)
            context.user_data.clear()
            keyboard = [
                [InlineKeyboardButton("📝 إنشاء حساب", callback_data="auth_register")],
                [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="auth_login")],
                [InlineKeyboardButton("🔑 نسيت كلمة المرور", callback_data="auth_forgot")],
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
        [InlineKeyboardButton("📒 دفتر الديون", callback_data="menu_customers")],
        [InlineKeyboardButton("📒 الدخل والمصروف", callback_data="menu_ledger")],
        [InlineKeyboardButton("👤 حسابي", callback_data="menu_profile")],
    ]
    if update.effective_user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("🔐 لوحة الأدمن", callback_data="admin_panel")])
    await query.edit_message_text(
        "القائمة الرئيسية 📒\n\nاختر ما تريد:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
