# -*- coding: utf-8 -*-
"""دفتر الحسابات: دخل ومصروف"""
from decimal import Decimal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import SessionLocal
from app_models import User, LedgerEntry, LedgerCategory

# حالات محادثة إضافة عملية (للمستخدم)
(ADD_AMOUNT, ADD_DESC, CAT_ADD_NAME, CAT_ADD_KIND) = range(4)

ICON_TAKEN = "🔴"
ICON_GIVEN = "🟢"

KIND_TAKEN = "took"
KIND_GIVEN = "gave"

DEFAULT_CATEGORIES = [
    ("راتبك الثابت", KIND_TAKEN),
    ("مدخولات اضافية", KIND_TAKEN),
    ("التزامات مصروفات", KIND_GIVEN),
]


def get_current_user(db, telegram_id: int):
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def _ensure_default_categories(db, user_id: int):
    existing = db.query(LedgerCategory).filter(LedgerCategory.user_id == user_id).count()
    if existing:
        return
    for name, kind in DEFAULT_CATEGORIES:
        db.add(LedgerCategory(user_id=user_id, name=name, kind=kind))
    db.commit()


def _cat_icon(kind: str) -> str:
    return ICON_TAKEN if kind == KIND_TAKEN else ICON_GIVEN


async def ledger_pick_category_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختيار صنف لتسجيل عملية (يدخل بعدها لخطوة إدخال المبلغ)."""
    query = update.callback_query
    await query.answer()

    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("يجب تسجيل الدخول أولاً. استخدم /start")
            return ConversationHandler.END

        _ensure_default_categories(db, user.id)

        # callback_data: ledger_pick_cat_<id>
        try:
            cat_id = int(query.data.replace("ledger_pick_cat_", "", 1))
        except Exception:
            await query.edit_message_text("صنف غير صالح.")
            return ConversationHandler.END

        cat = (
            db.query(LedgerCategory)
            .filter(LedgerCategory.user_id == user.id, LedgerCategory.id == cat_id)
            .first()
        )
        if not cat:
            await query.edit_message_text("صنف غير موجود.")
            return ConversationHandler.END

        context.user_data["ledger_category_id"] = cat.id
        context.user_data["ledger_category_kind"] = cat.kind
        context.user_data["ledger_category_name"] = cat.name

        await query.edit_message_text(
            f"أرسل المبلغ للصنف:\n{_cat_icon(cat.kind)} {cat.name}\n\n"
            "رقم فقط (مثال: 50000).",
        )
        return ADD_AMOUNT
    finally:
        db.close()


async def ledger_categories_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("يجب تسجيل الدخول أولاً. استخدم /start")
            return

        _ensure_default_categories(db, user.id)
        categories = (
            db.query(LedgerCategory)
            .filter(LedgerCategory.user_id == user.id)
            .order_by(LedgerCategory.created_at.desc())
            .all()
        )

        keyboard = [
            [InlineKeyboardButton("➕ إضافة صنف", callback_data="ledger_cat_add")],
            [InlineKeyboardButton("◀ رجوع", callback_data="menu_ledger")],
        ]

        if categories:
            for c in categories:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            f"{_cat_icon(c.kind)} {c.name}",
                            callback_data=f"ledger_pick_cat_{c.id}",
                        ),
                        InlineKeyboardButton(
                            "🗑 مسح",
                            callback_data=f"ledger_cat_del_req_{c.id}",
                        ),
                    ]
                )
        else:
            keyboard.append([InlineKeyboardButton("لا توجد أصناف بعد", callback_data="noop")])

        await query.edit_message_text(
            "📚 أصناف الصنف\n\n"
            "الصنف يحدد هل العملية تسجل كـ 🔴 أخذت أو 🟢 أعطيت.\n"
            "تگدر تضيف مباشرة باختيار الصنف.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def ledger_cat_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("ledger_cat_name", None)
    context.user_data.pop("ledger_cat_kind", None)
    await query.edit_message_text("أرسل اسم الصنف الجديد:")
    return CAT_ADD_NAME


async def ledger_cat_name_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("يرجى إرسال اسم صحيح للصنف.")
        return CAT_ADD_NAME
    context.user_data["ledger_cat_name"] = name

    keyboard = [
        [
            InlineKeyboardButton("🔴 أخذت", callback_data="ledger_cat_kind_took"),
            InlineKeyboardButton("🟢 أعطيت", callback_data="ledger_cat_kind_gave"),
        ]
    ]
    await update.message.reply_text("حدد نوع الصنف:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CAT_ADD_KIND


async def ledger_cat_kind_took_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_name = context.user_data.get("ledger_cat_name")
    if not cat_name:
        await query.edit_message_text("انتهت الجلسة. ابدأ من جديد.")
        return ConversationHandler.END

    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("غير مسموح.")
            return ConversationHandler.END
        db.add(LedgerCategory(user_id=user.id, name=cat_name, kind=KIND_TAKEN))
        db.commit()
    finally:
        db.close()

    # رجوع لقائمة الأصناف
    await ledger_categories_menu(update, context)
    return ConversationHandler.END


async def ledger_cat_kind_gave_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_name = context.user_data.get("ledger_cat_name")
    if not cat_name:
        await query.edit_message_text("انتهت الجلسة. ابدأ من جديد.")
        return ConversationHandler.END

    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("غير مسموح.")
            return ConversationHandler.END
        db.add(LedgerCategory(user_id=user.id, name=cat_name, kind=KIND_GIVEN))
        db.commit()
    finally:
        db.close()

    await ledger_categories_menu(update, context)
    return ConversationHandler.END


async def ledger_cat_del_req_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        cat_id = int(query.data.replace("ledger_cat_del_req_", "", 1))
    except Exception:
        await query.edit_message_text("صنف غير صالح.")
        return

    keyboard = [
        [InlineKeyboardButton("✅ تأكيد الحذف", callback_data=f"ledger_cat_del_do_{cat_id}")],
        [InlineKeyboardButton("↩ تراجع", callback_data="ledger_categories_menu")],
    ]
    await query.edit_message_text(
        "⚠️ هل أنت متأكد من حذف صنف الدفتر هذا؟\nلا يمكن التراجع.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def ledger_cat_del_do_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        cat_id = int(query.data.replace("ledger_cat_del_do_", "", 1))
    except Exception:
        await query.edit_message_text("صنف غير صالح.")
        return

    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("غير مسموح.")
            return

        cat = (
            db.query(LedgerCategory)
            .filter(LedgerCategory.user_id == user.id, LedgerCategory.id == cat_id)
            .first()
        )
        if cat:
            db.delete(cat)
            db.commit()
    finally:
        db.close()

    # رجوع لقائمة الأصناف
    await ledger_categories_menu(update, context)

async def menu_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("يجب تسجيل الدخول أولاً. استخدم /start")
            return
        # إنشاء أصناف افتراضية إذا كانت قاعدة المستخدم فاضية
        categories = (
            db.query(LedgerCategory)
            .filter(LedgerCategory.user_id == user.id)
            .order_by(LedgerCategory.created_at.asc())
            .all()
        )
        if not categories:
            for name, kind in DEFAULT_CATEGORIES:
                db.add(LedgerCategory(user_id=user.id, name=name, kind=kind))
            db.commit()
            categories = (
                db.query(LedgerCategory)
                .filter(LedgerCategory.user_id == user.id)
                .order_by(LedgerCategory.created_at.asc())
                .all()
            )

        entries = db.query(LedgerEntry).filter(LedgerEntry.user_id == user.id).all()
        total_taken = sum((float(e.amount) for e in entries if e.kind == "income"), 0.0)
        total_given = sum((float(e.amount) for e in entries if e.kind == "expense"), 0.0)
        balance = total_taken - total_given

        keyboard = []

        # أزرار إضافة مباشرة من أصناف الصنف
        for c in categories:
            icon = ICON_TAKEN if c.kind == KIND_TAKEN else ICON_GIVEN
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"{icon} {c.name}",
                        callback_data=f"ledger_pick_cat_{c.id}",
                    )
                ]
            )

        keyboard.append([InlineKeyboardButton("📚 أصناف الصنف", callback_data="ledger_categories_menu")])
        keyboard.append([InlineKeyboardButton("📋 آخر القيود", callback_data="ledger_list")])
        keyboard.append([InlineKeyboardButton("◀ دفتر الديون", callback_data="menu_customers")])

        text = (
            "الدخل والمصروف 📒\n\n"
            f"🔴 أخذت: {total_taken:.2f} د.ع.\n"
            f"🟢 أعطيت: {total_given:.2f} د.ع.\n"
            f"💰 الباقي: {balance:.2f} د.ع."
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
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
        [InlineKeyboardButton("⏭️ تخطي الوصف", callback_data="ledger_skip_desc_btn")],
    ]
    await update.message.reply_text(
        "اختياري: أرسل وصفاً للقيد.\n"
        "إذا تريد تخطي الوصف اضغط زر (تخطي الوصف).",
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

        cat_kind = context.user_data.get("ledger_category_kind", KIND_TAKEN)
        cat_name = context.user_data.get("ledger_category_name")
        ledger_kind = "income" if cat_kind == KIND_TAKEN else "expense"
        entry = LedgerEntry(
            user_id=user.id,
            kind=ledger_kind,
            amount=context.user_data.get("ledger_amount", 0),
            category=cat_name,
            description=desc or None,
        )
        db.add(entry)
        db.commit()
        kind_ar = "أخذت" if entry.kind == "income" else "أعطيت"
        await update.message.reply_text(
            f"تم تسجيل {kind_ar} بمبلغ {entry.amount} ✅\n"
            + (f"الوصف: {entry.description}" if entry.description else "")
        )
    finally:
        db.close()
    context.user_data.pop("ledger_category_kind", None)
    context.user_data.pop("ledger_category_name", None)
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
        all_entries = db.query(LedgerEntry).filter(LedgerEntry.user_id == user.id).all()
        total_taken = sum((float(e.amount) for e in all_entries if e.kind == "income"), 0.0)
        total_given = sum((float(e.amount) for e in all_entries if e.kind == "expense"), 0.0)
        balance = total_taken - total_given
        lines = [
            "📋 آخر 30 عملية:\n",
            f"🔴 أخذت: {total_taken:.2f} د.ع.",
            f"🟢 أعطيت: {total_given:.2f} د.ع.",
            f"💰 الباقي: {balance:.2f} د.ع.\n",
        ]
        for e in entries:
            icon = ICON_TAKEN if e.kind == "income" else ICON_GIVEN
            d = (e.description or "")[:40]
            cat = (e.category or "").strip()
            lines.append(
                f"{icon} {float(e.amount):.2f} — {cat} — {e.created_at.strftime('%Y-%m-%d')} {d}"
            )
        keyboard = [[InlineKeyboardButton("◀ دفتر الحسابات", callback_data="menu_ledger")]]
        await query.edit_message_text(
            "\n".join(lines) or "لا توجد سجلات بعد.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def ledger_skip_desc_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تخطي الوصف عبر زر بدل كتابة /skip"""
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("يجب تسجيل الدخول أولاً. استخدم /start")
            return ConversationHandler.END

        cat_kind = context.user_data.get("ledger_category_kind", KIND_TAKEN)
        cat_name = context.user_data.get("ledger_category_name")
        ledger_kind = "income" if cat_kind == KIND_TAKEN else "expense"
        entry = LedgerEntry(
            user_id=user.id,
            kind=ledger_kind,
            amount=context.user_data.get("ledger_amount", 0),
            category=cat_name,
            description=None,
        )
        db.add(entry)
        db.commit()
        kind_ar = "أخذت" if entry.kind == "income" else "أعطيت"
        await query.edit_message_text(f"تم تسجيل {kind_ar} بمبلغ {entry.amount} ✅")
    finally:
        db.close()
    context.user_data.pop("ledger_category_kind", None)
    context.user_data.pop("ledger_category_name", None)
    context.user_data.pop("ledger_amount", None)
    context.user_data.pop("ledger_skip", None)
    return ConversationHandler.END


async def ledger_skip_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ledger_skip"] = True
    return await ledger_add_desc(update, context)
