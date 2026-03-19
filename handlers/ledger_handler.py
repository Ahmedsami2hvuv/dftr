# -*- coding: utf-8 -*-
"""دفتر الحسابات: دخل ومصروف"""
from decimal import Decimal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import SessionLocal
from app_models import User, LedgerEntry

(LEDGER_MENU, ADD_KIND, ADD_CATEGORY, ADD_AMOUNT, ADD_DESC) = range(5)

CAT_FIXED_SALARY = "fixed_salary"
CAT_ADDITIONAL_INCOME = "additional_income"
CAT_EXPENSES = "expenses"


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

        # حساب إجمالي كل خانة
        fixed = sum(
            (e.amount for e in user.ledger_entries if (e.category or "") == CAT_FIXED_SALARY and e.kind == "income")
        )
        additional = sum(
            (e.amount for e in user.ledger_entries if (e.category or "") == CAT_ADDITIONAL_INCOME and e.kind == "income")
        )
        expenses = sum(
            (e.amount for e in user.ledger_entries if (e.category or "") == CAT_EXPENSES and e.kind == "expense")
        )
        net = fixed + additional - expenses

        keyboard = [
            [InlineKeyboardButton("➕ إضافة راتبك الثابت", callback_data="ledger_add_fixed_salary")],
            [InlineKeyboardButton("➕ إضافة مدخولات إضافية", callback_data="ledger_add_additional_income")],
            [InlineKeyboardButton("➕ إضافة التزامات/مصروفات", callback_data="ledger_add_expenses")],
            [InlineKeyboardButton("◀ دفتر الديون", callback_data="menu_customers")],
        ]

        text = (
            "الدخل والمصروف 📒\n\n"
            f"راتبك الثابت: {fixed} د.ع.\n"
            f"مدخولات إضافية: {additional} د.ع.\n"
            f"التزامات/مصروفات: {expenses} د.ع.\n\n"
            f"الصافي: {net} د.ع."
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()


async def ledger_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str, category: str):
    query = update.callback_query
    await query.answer()
    context.user_data["ledger_kind"] = kind
    context.user_data["ledger_category"] = category
    kind_ar = "دخل" if kind == "income" else "مصروف"
    await query.edit_message_text(
        f"إضافة {kind_ar} 💵\n\nأرسل المبلغ (رقم فقط، مثال: 50000):"
    )
    return ADD_AMOUNT


async def ledger_add_fixed_salary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await ledger_add_start(update, context, "income", CAT_FIXED_SALARY)


async def ledger_add_additional_income(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await ledger_add_start(update, context, "income", CAT_ADDITIONAL_INCOME)


async def ledger_add_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await ledger_add_start(update, context, "expense", CAT_EXPENSES)


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
    keyboard = [
        [InlineKeyboardButton("⏭️ سكيب الوصف", callback_data="ledger_skip_desc_btn")],
    ]
    await update.message.reply_text(
        "اختياري: أرسل وصفاً للقيد.\n"
        "إذا تريد تخطي الوصف اضغط زر (سكيب الوصف).",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ADD_DESC


async def ledger_add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip() if update.message.text else ""
    raw = (update.message.text or "").strip()
    if context.user_data.pop("ledger_skip", False) or raw in ("تخطى", "skip", "/skip"):
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
            category=context.user_data.get("ledger_category"),
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


async def ledger_skip_desc_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سكيب الوصف عبر زر بدل كتابة /skip"""
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("يجب تسجيل الدخول أولاً. استخدم /start")
            return ConversationHandler.END
        entry = LedgerEntry(
            user_id=user.id,
            kind=context.user_data.get("ledger_kind", "income"),
            amount=context.user_data.get("ledger_amount", 0),
            category=context.user_data.get("ledger_category"),
            description=None,
        )
        db.add(entry)
        db.commit()
        kind_ar = "دخل" if entry.kind == "income" else "مصروف"
        await query.edit_message_text(f"تم تسجيل {kind_ar} بمبلغ {entry.amount} ✅")
    finally:
        db.close()
    context.user_data.pop("ledger_kind", None)
    context.user_data.pop("ledger_amount", None)
    context.user_data.pop("ledger_category", None)
    context.user_data.pop("ledger_skip", None)
    return ConversationHandler.END


async def ledger_skip_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ledger_skip"] = True
    return await ledger_add_desc(update, context)
