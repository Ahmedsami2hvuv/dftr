# -*- coding: utf-8 -*-
"""لوحة الأدمن"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import SessionLocal
from app_models import User, LedgerEntry, Debt, Customer, CustomerTransaction
from config import ADMIN_ID

ADMIN_BROADCAST_TEXT = 900

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
            [InlineKeyboardButton("📢 بث / إذاعة", callback_data="admin_broadcast")],
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
        lines = ["المستخدمون (آخر 50):\nانقر على المستخدم لفتح ملفه."]
        keyboard = []
        for u in users:
            name = u.full_name or u.username or "بدون اسم"
            tg = f"id:{u.telegram_id}" if u.telegram_id else "بدون ربط"
            lines.append(
                f"• {name} | {u.phone or '—'} | {tg}"
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"👤 {name}"[:64],
                        callback_data=f"admin_user_{u.id}",
                    )
                ]
            )
        keyboard.append([InlineKeyboardButton("◀ لوحة الأدمن", callback_data="admin_panel")])
        await query.edit_message_text(
            "\n".join(lines) if len(lines) > 1 else "لا مستخدمين بعد.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def admin_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    uid = int(query.data.replace("admin_user_", ""))
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == uid).first()
        if not u:
            await query.edit_message_text("المستخدم غير موجود.")
            return

        # ملخص البيانات والديون
        debts_given = db.query(Debt).filter(Debt.from_user_id == u.id).all()
        they_owe = sum(float(d.amount or 0) for d in debts_given if int(d.is_they_owe_me or 0) == 1)
        i_owe = sum(float(d.amount or 0) for d in debts_given if int(d.is_they_owe_me or 0) == 0)

        entries = db.query(LedgerEntry).filter(LedgerEntry.user_id == u.id).all()
        total_income = sum(float(e.amount or 0) for e in entries if e.kind == "income")
        total_expense = sum(float(e.amount or 0) for e in entries if e.kind == "expense")

        customers = db.query(Customer).filter(Customer.user_id == u.id).all()
        customer_ids = [c.id for c in customers]
        tx_count = 0
        if customer_ids:
            tx_count = (
                db.query(CustomerTransaction)
                .filter(CustomerTransaction.customer_id.in_(customer_ids))
                .count()
            )

        name = u.full_name or u.username or "—"
        username_line = f"يوزر: @{u.username}\n" if u.username else "يوزر: —\n"
        text = "ملف المستخدم 👤\n\n" + f"الاسم: {name}\n" + username_line
        text += (
            f"الهاتف: {u.phone or '—'}\n"
            f"معرّف Telegram: {u.telegram_id or '—'}\n"
            f"تاريخ التسجيل: {u.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"دفتر الحسابات:\n"
            f"- الدخل: {total_income:.2f}\n"
            f"- المصروف: {total_expense:.2f}\n\n"
            f"دفتر الديون:\n"
            f"- له على الآخرين: {they_owe:.2f}\n"
            f"- عليه للآخرين: {i_owe:.2f}\n"
            f"- عدد العملاء: {len(customers)}\n"
            f"- عدد معاملات العملاء: {tx_count}"
        )

        keyboard = []
        if u.telegram_id:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "💬 مراسلة المستخدم على تليجرام",
                        url=f"tg://user?id={u.telegram_id}",
                    )
                ]
            )
        keyboard.append([InlineKeyboardButton("◀ قائمة المستخدمين", callback_data="admin_users")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()


async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    await query.edit_message_text(
        "إذاعة / بث 📢\n\n"
        "أرسل الآن نص الرسالة التي تريد إرسالها لكل المستخدمين.\n"
        "للإلغاء اكتب /cancel"
    )
    return ADMIN_BROADCAST_TEXT


async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("أرسل نصًا صحيحًا.")
        return ADMIN_BROADCAST_TEXT
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.telegram_id.isnot(None)).all()
    finally:
        db.close()

    sent = 0
    failed = 0
    for u in users:
        try:
            await context.bot.send_message(chat_id=u.telegram_id, text=text)
            sent += 1
        except Exception:
            failed += 1

    keyboard = [[InlineKeyboardButton("◀ لوحة الأدمن", callback_data="admin_panel")]]
    await update.message.reply_text(
        f"تم الإرسال ✅\n\nنجح: {sent}\nفشل: {failed}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ConversationHandler.END
