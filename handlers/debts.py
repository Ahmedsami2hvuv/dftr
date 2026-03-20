# -*- coding: utf-8 -*-
"""إدارة الديون"""
from decimal import Decimal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import SessionLocal
from app_models import User, Debt
from handlers.inline_nav import kb_main_menu

(DEBT_MENU, DEBT_WHO, DEBT_AMOUNT, DEBT_DIR, DEBT_DESC) = range(5)


def get_current_user(db, telegram_id: int):
    return db.query(User).filter(User.telegram_id == telegram_id).first()


async def debt_cancel_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء تسجيل دين والعودة لقائمة الديون."""
    query = update.callback_query
    context.user_data.pop("debt_to_name", None)
    context.user_data.pop("debt_amount", None)
    context.user_data.pop("debt_they_owe_me", None)
    await menu_debts(update, context)
    return ConversationHandler.END


async def menu_debts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["last_menu"] = "debts"
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text(
                "يجب تسجيل الدخول أولاً. استخدم /start",
                reply_markup=kb_main_menu(),
            )
            return
        keyboard = [
            [InlineKeyboardButton("➕ تسجيل دين (لصالحك)", callback_data="debt_add_they_owe")],
            [InlineKeyboardButton("➖ تسجيل دين (عليك)", callback_data="debt_add_i_owe")],
            [InlineKeyboardButton("📋 قائمة الديون", callback_data="debt_list")],
            [InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")],
        ]
        await query.edit_message_text(
            "الديون 💰\n\nاختر:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def debt_add_they_owe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["debt_they_owe_me"] = 1
    await query.edit_message_text(
        "تسجيل دين لصالحك (هم مدينون لك) 📥\n\n"
        "أرسل اسم الشخص أو الجهة (مثال: أحمد، أو محل الخضار):",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ رجوع", callback_data="menu_debts")]]
        ),
    )
    return DEBT_WHO


async def debt_add_i_owe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["debt_they_owe_me"] = 0
    await query.edit_message_text(
        "تسجيل دين عليك (أنت مدين) 📤\n\n"
        "أرسل اسم الشخص أو الجهة التي تدين لها:",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ رجوع", callback_data="menu_debts")]]
        ),
    )
    return DEBT_WHO


async def debt_who(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text(
            "أرسل الاسم.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀ رجوع", callback_data="menu_debts")]]
            ),
        )
        return DEBT_WHO
    context.user_data["debt_to_name"] = name
    await update.message.reply_text(
        "أرسل المبلغ (رقم فقط):",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ رجوع", callback_data="menu_debts")]]
        ),
    )
    return DEBT_AMOUNT


async def debt_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = Decimal(update.message.text.replace(",", "").strip())
        if amount <= 0:
            await update.message.reply_text(
                "أدخل مبلغاً أكبر من صفر.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("◀ رجوع", callback_data="menu_debts")]]
                ),
            )
            return DEBT_AMOUNT
    except Exception:
        await update.message.reply_text(
            "أدخل رقماً صحيحاً.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀ رجوع", callback_data="menu_debts")]]
            ),
        )
        return DEBT_AMOUNT
    context.user_data["debt_amount"] = amount
    keyboard = [
        [InlineKeyboardButton("⏭️ تخطي الوصف", callback_data="debt_skip_desc_btn")],
        [InlineKeyboardButton("◀ رجوع", callback_data="menu_debts")],
    ]
    await update.message.reply_text(
        "اختياري: وصف.\n"
        "إذا تريد تخطي الوصف اضغط زر (تخطي الوصف).",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return DEBT_DESC


async def debt_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    desc = raw
    if raw in ("تخطى", "skip", "/skip"):
        desc = ""
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await update.message.reply_text(
                "انتهت الجلسة. استخدم /start",
                reply_markup=kb_main_menu(),
            )
            return ConversationHandler.END
        debt = Debt(
            from_user_id=user.id,
            to_user_id=None,
            to_name=context.user_data.get("debt_to_name"),
            amount=context.user_data.get("debt_amount"),
            is_they_owe_me=context.user_data.get("debt_they_owe_me", 1),
            description=desc or None,
        )
        db.add(debt)
        db.commit()
        direction = "مدينون لك" if debt.is_they_owe_me else "أنت مدين"
        await update.message.reply_text(
            f"تم تسجيل الدين ✅\n{debt.to_name}: {debt.amount} — {direction}",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("◀ الديون", callback_data="menu_debts")],
                    [InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")],
                ]
            ),
        )
    finally:
        db.close()
    for k in ("debt_to_name", "debt_amount", "debt_they_owe_me"):
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def debt_skip_desc_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تخطي الوصف عبر زر بدل كتابة /skip"""
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text(
                "يجب تسجيل الدخول أولاً. استخدم /start",
                reply_markup=kb_main_menu(),
            )
            return ConversationHandler.END
        debt = Debt(
            from_user_id=user.id,
            to_user_id=None,
            to_name=context.user_data.get("debt_to_name"),
            amount=context.user_data.get("debt_amount"),
            is_they_owe_me=context.user_data.get("debt_they_owe_me", 1),
            description=None,
        )
        db.add(debt)
        db.commit()
        direction = "مدينون لك" if debt.is_they_owe_me else "أنت مدين"
        await query.edit_message_text(
            f"تم تسجيل الدين ✅\n{debt.to_name}: {debt.amount} — {direction}",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("◀ الديون", callback_data="menu_debts")],
                    [InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")],
                ]
            ),
        )
    finally:
        db.close()
    for k in ("debt_to_name", "debt_amount", "debt_they_owe_me"):
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def debt_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text(
                "يجب تسجيل الدخول أولاً.",
                reply_markup=kb_main_menu(),
            )
            return
        debts = (
            db.query(Debt)
            .filter(Debt.from_user_id == user.id)
            .order_by(Debt.created_at.desc())
            .limit(40)
        ).all()
        they_owe = sum(d.amount for d in debts if d.is_they_owe_me)
        i_owe = sum(d.amount for d in debts if not d.is_they_owe_me)
        net = they_owe - i_owe
        lines = [
            "قائمة الديون 💰\n",
            f"ما عليهم لك: {they_owe}",
            f"ما عليك لهم: {i_owe}",
            f"صافي (لصالحك إذا موجب): {net}\n",
        ]
        for d in debts:
            arrow = "←" if d.is_they_owe_me else "→"
            lines.append(f"{arrow} {d.to_name or '?'}: {d.amount} — {d.created_at.strftime('%Y-%m-%d')}")
        keyboard = [[InlineKeyboardButton("◀ الديون", callback_data="menu_debts")]]
        await query.edit_message_text(
            "\n".join(lines) or "لا توجد ديون مسجلة.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
