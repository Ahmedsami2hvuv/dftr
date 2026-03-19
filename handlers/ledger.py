# -*- coding: utf-8 -*-
"""دفتر الحسابات: دخل ومصروف"""
from decimal import Decimal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import SessionLocal
from models import User, LedgerEntry

(LEDGER_MENU, ADD_KIND, ADD_AMOUNT, ADD_DESC) = range(4)


def get_current_user(db, telegram_id: int):
    return db.query(User).filter(User.telegram_id == telegram_id).first()


async def menu_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("يجب تسجيل الدخول أولاً. استخدم /start")
            return
        keyboard = [
            [InlineKeyboardButton("➕ إضافة دخل", callback_data="ledger_add_income")],
            [InlineKeyboardButton("➖ إضافة مصروف", callback_data="ledger_add_expense")],
            [InlineKeyboardButton("📋 عرض السجل", callback_data="ledger_list")],
            [InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")],
        ]
        await query.edit_message_text(
            "دفتر الحسابات 📊\n\nاختر:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def ledger_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    query = update.callback_query
    await query.answer()
    context.user_data["ledger_kind"] = kind
    kind_ar = "دخل" if kind == "income" else "مصروف"
    await query.edit_message_text(f"إضافة {kind_ar} 💵\n\nأرسل المبلغ (رقم فقط، مثال: 50000):")
    return ADD_AMOUNT


async def ledger_add_income(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await ledger_add_start(update, context, "income")


async def ledger_add_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await ledger_add_start(update, context, "expense")


async def ledger_add_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = Decimal(update.message.text.replace(",", "").strip())
        if amount <= 0:
            await update.message.reply_text("أدخل مبلغاً أكبر من صفر.")
            return ADD_AMOUNT
    except Exception:
        await update.message.reply_text("أدخل رقماً صحيحاً للمبلغ.")
        return ADD_AMOUNT
    context.user_data["ledger_amount"] = amount
    await update.message.reply_text("اختياري: أرسل وصفاً للقيد (أو /تخطى أو /skip):")
    return ADD_DESC


async def ledger_add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip() if update.message.text else ""
    if context.user_data.pop("ledger_skip", False) or (update.message.text and update.message.text.strip() == "/تخطى"):
        desc = ""
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await update.message.reply_text("انتهت الجلسة. استخدم /start")
            return ConversationHandler.END
        entry = LedgerEntry(
            user_id=user.id,
            kind=context.user_data.get("ledger_kind", "income"),
            amount=context.user_data.get("ledger_amount", 0),
            description=desc or None,
        )
        db.add(entry)
        db.commit()
        kind_ar = "دخل" if entry.kind == "income" else "مصروف"
        await update.message.reply_text(
            f"تم تسجيل {kind_ar} بمبلغ {entry.amount} ✅\n"
            + (f"الوصف: {entry.description}" if entry.description else "")
        )
    finally:
        db.close()
    context.user_data.pop("ledger_kind", None)
    context.user_data.pop("ledger_amount", None)
    return ConversationHandler.END


async def ledger_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("يجب تسجيل الدخول أولاً.")
            return
        entries = (
            db.query(LedgerEntry)
            .filter(LedgerEntry.user_id == user.id)
            .order_by(LedgerEntry.created_at.desc())
            .limit(30)
        ).all()
        total_income = sum(e.amount for e in user.ledger_entries if e.kind == "income")
        total_expense = sum(e.amount for e in user.ledger_entries if e.kind == "expense")
        balance = total_income - total_expense
        lines = [
            f"📊 آخر 30 قيد:\n",
            f"إجمالي الدخل: {total_income}",
            f"إجمالي المصروف: {total_expense}",
            f"الرصيد: {balance}\n",
        ]
        for e in entries:
            k = "➕" if e.kind == "income" else "➖"
            d = (e.description or "")[:30]
            lines.append(f"{k} {e.amount} — {e.created_at.strftime('%Y-%m-%d')} {d}")
        keyboard = [[InlineKeyboardButton("◀ دفتر الحسابات", callback_data="menu_ledger")]]
        await query.edit_message_text(
            "\n".join(lines) or "لا توجد سجلات بعد.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def ledger_skip_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ledger_skip"] = True
    return await ledger_add_desc(update, context)
