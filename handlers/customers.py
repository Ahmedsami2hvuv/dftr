# -*- coding: utf-8 -*-
"""دفتر الديون: عملاء، أخذت/أعطيت، مشاركة"""
import secrets
from urllib.parse import quote
from decimal import Decimal
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import SessionLocal
from app_models import User, Customer, CustomerTransaction, ShareLink, CustomerCategory
from utils.phone import normalize_phone, wa_number
from config import WEB_BASE_URL

(
    CUST_NAME,
    CUST_PHONE,
    CUST_AMOUNT,
    CUST_NOTE,
    CUST_EDIT_NAME,
    CUST_EDIT_PHONE,
) = range(6)

TX_PAGE_SIZE = 15

(TX_EDIT_AMOUNT, TX_EDIT_NOTE, TX_EDIT_DATE, TX_EDIT_PHOTO) = range(4)

(CAT_ADD_NAME, CAT_ADD_KIND) = range(200, 202)


def get_current_user(db, telegram_id: int):
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def _balance(customer):
    gave = sum(t.amount for t in customer.transactions if t.kind == "gave")
    took = sum(t.amount for t in customer.transactions if t.kind == "took")
    return float(gave - took), float(gave), float(took)


async def menu_customer_categories(update: Update, context: ContextTypes.DEFAULT_TYPE, back_customer_id: int):
    """عرض أصناف الصنف + إضافة/مسح"""
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("يجب تسجيل الدخول أولاً. استخدم /start")
            return

        context.user_data["cust_cat_back_customer_id"] = back_customer_id
        cats = (
            db.query(CustomerCategory)
            .filter(CustomerCategory.user_id == user.id)
            .order_by(CustomerCategory.created_at.desc())
            .all()
        )

        keyboard = [
            [InlineKeyboardButton("➕ إضافة صنف", callback_data="cust_cat_add")],
            [InlineKeyboardButton("◀ رجوع", callback_data=f"cust_{back_customer_id}")],
        ]

        if cats:
            for c in cats:
                icon = "🔴" if c.kind == "took" else "🟢"
                keyboard.append(
                    [
                        InlineKeyboardButton(f"{icon} {c.name}", callback_data="noop"),
                        InlineKeyboardButton("🗑 مسح", callback_data=f"cust_cat_del_req_{c.id}"),
                    ]
                )
        else:
            keyboard.append([InlineKeyboardButton("لا توجد أصناف بعد", callback_data="noop")])

        await query.edit_message_text(
            "📚 أصناف الصنف\n\n"
            "الصنف يحدد نوع المعاملة: 🔴 أخذت أو 🟢 أعطيت.\n\n"
            "اختر إضافة أو مسح صنف.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def cust_cat_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إضافة صنف جديد"""
    query = update.callback_query
    await query.answer()
    context.user_data.pop("cust_cat_add_name", None)
    context.user_data.pop("cust_cat_add_kind", None)
    await query.edit_message_text("أرسل اسم الصنف الجديد:")
    return CAT_ADD_NAME


async def cust_cat_name_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("يرجى إرسال اسم صحيح للصنف.")
        return CAT_ADD_NAME
    context.user_data["cust_cat_add_name"] = name

    keyboard = [
        [
            InlineKeyboardButton("🔴 أخذت (took)", callback_data="cust_cat_kind_took"),
            InlineKeyboardButton("🟢 أعطيت (gave)", callback_data="cust_cat_kind_gave"),
        ]
    ]
    await update.message.reply_text("حدد نوع الصنف:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CAT_ADD_KIND


async def cust_cat_kind_took_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    back_cid = context.user_data.get("cust_cat_back_customer_id")
    name = context.user_data.get("cust_cat_add_name")
    if not back_cid or not name:
        await query.edit_message_text("انتهت الجلسة. ابدأ من جديد.")
        return ConversationHandler.END

    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("غير مسموح.")
            return ConversationHandler.END
        db.add(CustomerCategory(user_id=user.id, name=name, kind="took"))
        db.commit()
    finally:
        db.close()

    # رجوع للقائمة
    await menu_customer_categories(update, context, int(back_cid))
    context.user_data.pop("cust_cat_add_name", None)
    return ConversationHandler.END


async def cust_cat_kind_gave_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    back_cid = context.user_data.get("cust_cat_back_customer_id")
    name = context.user_data.get("cust_cat_add_name")
    if not back_cid or not name:
        await query.edit_message_text("انتهت الجلسة. ابدأ من جديد.")
        return ConversationHandler.END

    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("غير مسموح.")
            return ConversationHandler.END
        db.add(CustomerCategory(user_id=user.id, name=name, kind="gave"))
        db.commit()
    finally:
        db.close()

    await menu_customer_categories(update, context, int(back_cid))
    context.user_data.pop("cust_cat_add_name", None)
    return ConversationHandler.END


async def cust_cat_del_req_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.replace("cust_cat_del_req_", ""))
    back_cid = context.user_data.get("cust_cat_back_customer_id")
    keyboard = [
        [
            InlineKeyboardButton(
                "✅ تأكيد الحذف",
                callback_data=f"cust_cat_del_do_{cat_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "↩ تراجع",
                callback_data=f"cust_categories_menu_{back_cid}",
            )
        ],
    ]
    await query.edit_message_text(
        "هل أنت متأكد من حذف هذا الصنف؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cust_cat_del_do_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.replace("cust_cat_del_do_", ""))
    back_cid = context.user_data.get("cust_cat_back_customer_id")
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("غير مسموح.")
            return
        cat = (
            db.query(CustomerCategory)
            .filter(CustomerCategory.id == cat_id, CustomerCategory.user_id == user.id)
            .first()
        )
        if cat:
            db.delete(cat)
            db.commit()
    finally:
        db.close()

    await menu_customer_categories(update, context, int(back_cid))


async def menu_customers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة دفتر الديون: إضافة عميل + قائمة العملاء"""
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("يجب تسجيل الدخول أولاً. استخدم /start")
            return
        customers = db.query(Customer).filter(Customer.user_id == user.id).order_by(Customer.created_at.desc()).all()
        keyboard = [[InlineKeyboardButton("➕ إضافة عميل", callback_data="cust_add")]]
        for c in customers:
            bal, _, _ = _balance(c)
            # بالواجهة: اسم العميل فقط بدون رقم
            label = f"{'🔴' if bal > 0 else '🟢'} {c.name}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"cust_{c.id}")])
        keyboard.append([InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")])
        await query.edit_message_text(
            "دفتر الديون 📒\n\nاختر عميلاً أو أضف عميلاً جديداً:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def cust_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("إضافة عميل 📝\n\nأرسل اسم العميل (إجباري):")
    return CUST_NAME


async def cust_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("يرجى إرسال اسم العميل.")
        return CUST_NAME
    context.user_data["cust_name"] = name
    await update.message.reply_text(
        "تم ✅\n\nأرسل رقم هاتف العميل (اختياري).\n"
        "إذا تريد تخطي الرقم اضغط زر السكيب.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⏭️ سكيب الرقم", callback_data="cust_phone_skip_btn")]]
        ),
    )
    return CUST_PHONE


async def cust_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    phone = normalize_phone(raw)
    if len(phone) < 10:
        await update.message.reply_text("رقم غير صحيح. ارسل رقم صحيح أو اضغط سكيب.")
        return CUST_PHONE
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await update.message.reply_text("انتهت الجلسة. استخدم /start")
            context.user_data.pop("cust_name", None)
            return ConversationHandler.END
        c = Customer(user_id=user.id, name=context.user_data["cust_name"], phone=phone)
        db.add(c)
        db.commit()
        keyboard = [
            [InlineKeyboardButton("عرض العميل", callback_data=f"cust_{c.id}")],
            [InlineKeyboardButton("◀ قائمة العملاء", callback_data="menu_customers")],
        ]
        await update.message.reply_text(
            f"تمت إضافة العميل ✅ {c.name}" + (f" — {c.phone}" if c.phone else ""),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
    context.user_data.pop("cust_name", None)
    return ConversationHandler.END


async def cust_phone_skip_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تخطي رقم الهاتف عبر زر بدل كتابة تخطى"""
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text("انتهت الجلسة. استخدم /start")
            return ConversationHandler.END
        c = Customer(user_id=user.id, name=context.user_data["cust_name"], phone=None)
        db.add(c)
        db.commit()
        keyboard = [
            [InlineKeyboardButton("عرض العميل", callback_data=f"cust_{c.id}")],
            [InlineKeyboardButton("◀ قائمة العملاء", callback_data="menu_customers")],
        ]
        await query.edit_message_text(
            f"تمت إضافة العميل ✅ {c.name}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
    context.user_data.pop("cust_name", None)
    return ConversationHandler.END


def _tx_kind_ar(kind: str) -> str:
    # عرض نوع المعاملة بدوائر فقط حسب طلبك
    return "🔴" if kind == "took" else "🟢"


async def _safe_edit_callback_text(callback_query, text: str, keyboard):
    """حاول تعديل النص، وإذا كان زر على صورة عدّل الكابشن بدل النص."""
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard is not None else None
    try:
        await callback_query.edit_message_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            # لرسائل الصور: نعدّل الكابشن بدل النص
            await callback_query.edit_message_caption(text, reply_markup=reply_markup)
        except Exception:
            # fallback: أرسل رسالة جديدة حتى ما يتعطل الزر
            await callback_query.message.reply_text(
                text,
                reply_markup=reply_markup,
            )


async def _build_customer_view(db, cust: Customer, offset: int):
    bal, gave, took = _balance(cust)
    cur = "د.ع."
    balance_text = f"الرصيد الحالي: {bal:.2f} {cur}"

    total = (
        db.query(CustomerTransaction)
        .filter(CustomerTransaction.customer_id == cust.id)
        .count()
    )
    txs = (
        db.query(CustomerTransaction)
        .filter(CustomerTransaction.customer_id == cust.id)
        .order_by(CustomerTransaction.created_at.desc())
        .offset(offset)
        .limit(TX_PAGE_SIZE)
        .all()
    )

    # الرصيد الجاري لكل معاملة (بالترتيب الزمني: الأقدم -> الأحدث)
    all_txs_asc = (
        db.query(CustomerTransaction)
        .filter(CustomerTransaction.customer_id == cust.id)
        .order_by(CustomerTransaction.created_at.asc(), CustomerTransaction.id.asc())
        .all()
    )
    running = 0.0
    running_after_by_tx = {}
    for rt in all_txs_asc:
        amt = float(rt.amount or 0)
        if rt.kind == "gave":
            running += amt
        else:  # took
            running -= amt
        running_after_by_tx[rt.id] = running

    text = (
        f"📒 {cust.name}\n"
        + (f"📞 {cust.phone}\n" if cust.phone else "")
        + f"\n{balance_text}\n"
        + f"🟢 أعطيت: {gave:.2f} {cur}\n"
        + f"🔴 أخذت: {took:.2f} {cur}\n\n"
        + "آخر المعاملات:"
    )

    has_more = offset + TX_PAGE_SIZE < total
    keyboard = []

    # معاملات قابلة للنقر
    if not txs:
        keyboard.append([InlineKeyboardButton("لا توجد معاملات بعد", callback_data="noop")])
    else:
        for t in txs:
            # تاريخ مختصر: يوم/شهر فقط
            dt = t.created_at.strftime("%d/%m")
            note = (t.note or "").strip()
            note_short = (note[:10] + "…") if len(note) > 10 else note
            icon = _tx_kind_ar(t.kind)
            amount_str = f"{float(t.amount):.2f}"
            remain = running_after_by_tx.get(t.id, bal)
            remain_str = f"{remain:.2f}"
            note_part = note_short if note_short else "—"
            # اللون ثم المبلغ ثم الملاحظة ثم رمز الرصيد ثم التاريخ
            label = f"{icon} {amount_str} | {note_part} | 💰 {remain_str} | {dt}"
            keyboard.append([InlineKeyboardButton(label[:64], callback_data=f"cust_tx_{t.id}")])

    # زر أخذت/أعطيت في آخر المعاملات
    keyboard.append(
        [
            InlineKeyboardButton("🔴 أخذت", callback_data=f"cust_took_{cust.id}"),
            InlineKeyboardButton("🟢 أعطيت", callback_data=f"cust_gave_{cust.id}"),
        ]
    )

    # عرض الباقيات بجانب تعديل الحساب
    edit_btn = InlineKeyboardButton("✏️ تعديل الحساب", callback_data=f"cust_edit_{cust.id}")
    share_btn = InlineKeyboardButton("📤 مشاركة", callback_data=f"cust_share_{cust.id}")
    if has_more:
        more_btn = InlineKeyboardButton(
            "➕ عرض الباقيات",
            callback_data=f"cust_tx_more_{cust.id}_{offset + TX_PAGE_SIZE}",
        )
        keyboard.append([more_btn])
        keyboard.append([edit_btn, share_btn])
    else:
        keyboard.append([edit_btn, share_btn])

    keyboard.append([InlineKeyboardButton("◀ قائمة العملاء", callback_data="menu_customers")])
    return text, keyboard


async def customer_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, customer_id: int, offset: int = 0):
    """عرض تفاصيل عميل + آخر 15 معاملة قابلة للنقر"""
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.id == customer_id).first()
        if not cust:
            await update.callback_query.edit_message_text("العميل غير موجود.")
            return
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await update.callback_query.edit_message_text("غير مسموح.")
            return
        text, keyboard = await _build_customer_view(db, cust, offset)
        await _safe_edit_callback_text(update.callback_query, text, keyboard)
    finally:
        db.close()


def _format_tx_amount(amount) -> str:
    try:
        return f"{float(amount):.2f}"
    except Exception:
        return str(amount)


async def _render_tx_detail(db, tx: CustomerTransaction):
    cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
    icon = _tx_kind_ar(tx.kind)
    kind_text = "أخذت" if tx.kind == "took" else "أعطيت"
    dt = tx.created_at.strftime("%d/%m/%Y %H:%M")
    note = (tx.note or "").strip()
    has_photo = bool(getattr(tx, "photo_file_id", None))

    text = (
        "🧾 تفاصيل المعاملة\n\n"
        f"العميل: {cust.name}\n"
        f"النوع: {icon} {kind_text}\n"
        f"السعر/المبلغ: {tx.amount} د.ع.\n"
        f"الملاحظة: {note if note else '—'}\n"
        f"التاريخ: {dt}\n"
        + ("الصورة: موجودة ✅" if has_photo else "الصورة: غير مضافة")
    )

    # 6 ازرار (ثلاثة بسطرين): مبلغ/ملاحظة/تاريخ ثم صورة/نوع/حذف
    keyboard = [
        [
            InlineKeyboardButton("✏️المبلغ", callback_data=f"cust_tx_edit_amount_{tx.id}"),
            InlineKeyboardButton("✏️الملاحظة", callback_data=f"cust_tx_edit_note_{tx.id}"),
            InlineKeyboardButton("✏️التاريخ", callback_data=f"cust_tx_edit_date_{tx.id}"),
        ],
        [
            InlineKeyboardButton("🖼الصورة", callback_data=f"cust_tx_edit_photo_{tx.id}"),
            InlineKeyboardButton("النوع", callback_data=f"cust_tx_toggle_kind_{tx.id}"),
            InlineKeyboardButton("حذف", callback_data=f"cust_tx_delete_req_{tx.id}"),
        ],
        [InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cust.id}")],
    ]
    return text, keyboard


async def cust_tx_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, tx_id: int):
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await query.edit_message_text("المعاملة غير موجودة.")
            return
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text("غير مسموح.")
            return
        text, keyboard = await _render_tx_detail(db, tx)
        # نعرض التفاصيل أولاً، ثم الأزرار برسالة منفصلة (حسب طلب المستخدم).
        if getattr(tx, "photo_file_id", None):
            await context.bot.send_photo(
                chat_id=update.effective_user.id,
                photo=tx.photo_file_id,
                caption=text,
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=text,
            )

        # بعدها نعرض الأزرار في رسالة مستقلة
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text="اختر الإجراء المطلوب:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        # نخلي رسالة الزر القديمة قصيرة بدون أزرار
        try:
            await query.edit_message_text("تم عرض تفاصيل المعاملة ✅")
        except Exception:
            pass
    finally:
        db.close()


async def cust_tx_delete_req_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب حذف مع تأكيد من المستخدم"""
    query = update.callback_query
    await query.answer()
    tx_id = int(query.data.replace("cust_tx_delete_req_", ""))
    # نستخدم زر الرجوع للقائمة/التفاصيل بدون حذف
    keyboard = [
        [
            InlineKeyboardButton(
                "✅ تأكيد الحذف",
                callback_data=f"cust_tx_delete_do_{tx_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "↩ تراجع",
                callback_data=f"cust_tx_{tx_id}",
            )
        ],
    ]
    await query.edit_message_text(
        "هل أنت متأكد من حذف هذه المعاملة؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cust_tx_delete_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data.startswith("cust_tx_delete_do_"):
        tx_id = int(data.replace("cust_tx_delete_do_", ""))
    else:
        # دعم قديم إن وجد
        tx_id = int(data.replace("cust_tx_delete_", ""))
    db = SessionLocal()
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await query.edit_message_text("المعاملة غير موجودة.")
            return
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text("غير مسموح.")
            return
        db.delete(tx)
        db.commit()
        text, keyboard = await _build_customer_view(db, cust, offset=0)
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()


async def cust_tx_toggle_kind_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tx_id = int(query.data.replace("cust_tx_toggle_kind_", ""))
    db = SessionLocal()
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await query.edit_message_text("المعاملة غير موجودة.")
            return
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text("غير مسموح.")
            return
        tx.kind = "gave" if tx.kind == "took" else "took"
        db.commit()
        text, keyboard = await _render_tx_detail(db, tx)
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()


async def cust_tx_edit_amount_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tx_id = int(query.data.replace("cust_tx_edit_amount_", ""))
    context.user_data["tx_edit_id"] = tx_id
    await query.edit_message_text("أرسل المبلغ الجديد (رقم فقط مثال: 82.75):")
    return TX_EDIT_AMOUNT


async def cust_tx_edit_amount_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = Decimal((update.message.text or "").replace(",", "").strip())
        if amount <= 0:
            await update.message.reply_text("أدخل مبلغاً أكبر من صفر.")
            return TX_EDIT_AMOUNT
    except Exception:
        await update.message.reply_text("أدخل رقماً صحيحاً.")
        return TX_EDIT_AMOUNT

    db = SessionLocal()
    tx_id = context.user_data.get("tx_edit_id")
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await update.message.reply_text("المعاملة غير موجودة.")
            return ConversationHandler.END
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await update.message.reply_text("غير مسموح.")
            return ConversationHandler.END
        tx.amount = amount
        db.commit()
        text, keyboard = await _render_tx_detail(db, tx)
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()
    context.user_data.pop("tx_edit_id", None)
    return ConversationHandler.END


async def cust_tx_edit_note_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tx_id = int(query.data.replace("cust_tx_edit_note_", ""))
    context.user_data["tx_edit_id"] = tx_id
    await query.edit_message_text("أرسل الملاحظة الجديدة (أو اكتب: حذف لحذفها):")
    return TX_EDIT_NOTE


async def cust_tx_edit_note_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    note = None if raw.lower() in ("حذف", "delete") else raw

    db = SessionLocal()
    tx_id = context.user_data.get("tx_edit_id")
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await update.message.reply_text("المعاملة غير موجودة.")
            return ConversationHandler.END
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await update.message.reply_text("غير مسموح.")
            return ConversationHandler.END
        tx.note = note
        db.commit()
        text, keyboard = await _render_tx_detail(db, tx)
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()
    context.user_data.pop("tx_edit_id", None)
    return ConversationHandler.END


async def cust_tx_edit_date_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tx_id = int(query.data.replace("cust_tx_edit_date_", ""))
    context.user_data["tx_edit_id"] = tx_id
    await query.edit_message_text("أرسل التاريخ الجديد بصيغة YYYY-MM-DD (مثال: 2026-03-19):")
    return TX_EDIT_DATE


async def cust_tx_edit_date_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
    except Exception:
        await update.message.reply_text("صيغة التاريخ غير صحيحة. استخدم YYYY-MM-DD.")
        return TX_EDIT_DATE

    db = SessionLocal()
    tx_id = context.user_data.get("tx_edit_id")
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await update.message.reply_text("المعاملة غير موجودة.")
            return ConversationHandler.END
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await update.message.reply_text("غير مسموح.")
            return ConversationHandler.END
        tx.created_at = dt
        db.commit()
        text, keyboard = await _render_tx_detail(db, tx)
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()
    context.user_data.pop("tx_edit_id", None)
    return ConversationHandler.END


async def cust_tx_edit_photo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tx_id = int(query.data.replace("cust_tx_edit_photo_", ""))
    context.user_data["tx_edit_id"] = tx_id
    keyboard = [[InlineKeyboardButton("◀ رجوع", callback_data=f"cust_tx_edit_photo_back_{tx_id}")]]
    await query.edit_message_text(
        "أرسل الصورة الآن (Photo).\n\nإذا تريد ترجع اضغط زر الرجوع.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return TX_EDIT_PHOTO


async def cust_tx_edit_photo_back_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tx_id = int(query.data.replace("cust_tx_edit_photo_back_", ""))
    db = SessionLocal()
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await query.edit_message_text("المعاملة غير موجودة.")
            return ConversationHandler.END
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text("غير مسموح.")
            return ConversationHandler.END
        text, keyboard = await _render_tx_detail(db, tx)
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()
    context.user_data.pop("tx_edit_id", None)
    return ConversationHandler.END


async def cust_tx_edit_photo_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    tx_id = context.user_data.get("tx_edit_id")
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await update.message.reply_text("المعاملة غير موجودة.")
            return ConversationHandler.END
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await update.message.reply_text("غير مسموح.")
            return ConversationHandler.END
        if not update.message.photo:
            await update.message.reply_text("لم تصل صورة. حاول مرة أخرى.")
            return TX_EDIT_PHOTO
        file_id = update.message.photo[-1].file_id
        tx.photo_file_id = file_id
        db.commit()
        # بعد الحفظ: أرسل الصورة + تفاصيلها فوراً
        text, keyboard = await _render_tx_detail(db, tx)
        await context.bot.send_photo(
            chat_id=update.effective_user.id,
            photo=file_id,
            caption=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
    context.user_data.pop("tx_edit_id", None)
    return ConversationHandler.END


async def cust_took(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أخذت (باللون الأحمر)"""
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_took_", ""))
    context.user_data["cust_txn_kind"] = "took"
    context.user_data["cust_txn_cid"] = cid
    keyboard = [
        [InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_txn_back_{cid}")],
        [InlineKeyboardButton("❌ إلغاء وخروج", callback_data="cust_txn_cancel")],
    ]
    await query.edit_message_text(
        "أخذت 🔴\n\nأرسل المبلغ (رقم):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CUST_AMOUNT


async def cust_gave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أعطيت (باللون الأخضر) — العميل مدين"""
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_gave_", ""))
    context.user_data["cust_txn_kind"] = "gave"
    context.user_data["cust_txn_cid"] = cid
    keyboard = [
        [InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_txn_back_{cid}")],
        [InlineKeyboardButton("❌ إلغاء وخروج", callback_data="cust_txn_cancel")],
    ]
    await query.edit_message_text(
        "أعطيت 🟢\n\nأرسل المبلغ (رقم):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CUST_AMOUNT


async def cust_txn_back_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رجوع للعميل وإلغاء حالة إدخال المعاملة."""
    query = update.callback_query
    await query.answer()
    try:
        cid = int(query.data.replace("cust_txn_back_", ""))
    except Exception:
        await query.edit_message_text("غير قادر على الرجوع.")
        return ConversationHandler.END

    for k in ("cust_txn_kind", "cust_txn_cid", "cust_txn_amount", "cust_txn_note_text", "cust_txn_photo_file_id"):
        context.user_data.pop(k, None)

    await customer_detail(update, context, cid, offset=0)
    return ConversationHandler.END


async def cust_txn_cancel_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء العملية والعودة لقائمة العملاء."""
    query = update.callback_query
    await query.answer()
    for k in ("cust_txn_kind", "cust_txn_cid", "cust_txn_amount", "cust_txn_note_text", "cust_txn_photo_file_id"):
        context.user_data.pop(k, None)
    await menu_customers(update, context)
    return ConversationHandler.END


async def cust_txn_back_amount_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رجوع لتعديل السعر من خطوة الملاحظة/الصورة."""
    query = update.callback_query
    await query.answer()
    cid = context.user_data.get("cust_txn_cid")
    if not cid:
        await menu_customers(update, context)
        return ConversationHandler.END
    keyboard = [
        [InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_txn_back_{cid}")],
        [InlineKeyboardButton("❌ إلغاء وخروج", callback_data="cust_txn_cancel")],
    ]
    await _safe_edit_callback_text(
        query,
        "رجوع لتعديل السعر.\n\nأرسل المبلغ الجديد (رقم فقط):",
        keyboard,
    )
    return CUST_AMOUNT


async def cust_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = Decimal((update.message.text or "").replace(",", "").strip())
        if amount <= 0:
            await update.message.reply_text("أدخل مبلغاً أكبر من صفر.")
            return CUST_AMOUNT
    except Exception:
        await update.message.reply_text("أدخل رقماً صحيحاً.")
        return CUST_AMOUNT
    context.user_data["cust_txn_amount"] = amount
    cid = context.user_data.get("cust_txn_cid")
    keyboard = [
        [InlineKeyboardButton("⏭️ سكيب الملاحظة", callback_data="cust_note_skip_btn")],
        [
            InlineKeyboardButton("↩ رجوع لتعديل السعر", callback_data="cust_txn_back_amount"),
            InlineKeyboardButton("❌ إلغاء وخروج", callback_data="cust_txn_cancel"),
        ],
    ]
    await update.message.reply_text(
        "أرسل ملاحظة، و(إذا تريد) صورة.\n"
        "إذا أرسلت صورة بدون ملاحظة كمل وارسل الملاحظة نصاً.\n"
        "يمكنك أيضاً استخدام زر (سكيب الملاحظة).",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CUST_NOTE


async def cust_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # إذا كانت الملاحظة أُرسلت عبر caption للصور
    note = context.user_data.get("cust_txn_note_text") or (update.message.text or "").strip()
    db = SessionLocal()
    try:
        cid = context.user_data.get("cust_txn_cid")
        kind = context.user_data.get("cust_txn_kind")
        amount = context.user_data.get("cust_txn_amount")
        photo_file_id = context.user_data.get("cust_txn_photo_file_id")
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not cust:
            await update.message.reply_text("العميل غير موجود.")
            return ConversationHandler.END
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await update.message.reply_text("غير مسموح.")
            return ConversationHandler.END
        t = CustomerTransaction(
            customer_id=cid,
            amount=amount,
            kind=kind,
            note=note or None,
            photo_file_id=photo_file_id,
        )
        db.add(t)
        db.commit()
        text, keyboard = await _build_customer_view(db, cust, offset=0)
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
    for k in (
        "cust_txn_kind",
        "cust_txn_cid",
        "cust_txn_amount",
        "cust_txn_note_text",
        "cust_txn_photo_file_id",
    ):
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def cust_note_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام صورة الملاحظة داخل نفس خطوة CUST_NOTE."""
    if not update.message.photo:
        return CUST_NOTE

    file_id = update.message.photo[-1].file_id
    context.user_data["cust_txn_photo_file_id"] = file_id

    # إذا المستخدم كتب caption فاعتبره ملاحظة مباشرة
    caption = (update.message.caption or "").strip() if update.message.caption else ""
    if caption:
        context.user_data["cust_txn_note_text"] = caption
        # نفذ الحفظ باستخدام cust_note مع قراءة الملاحظة من context
        return await cust_note(update, context)

    cid = context.user_data.get("cust_txn_cid")
    keyboard = [
        [InlineKeyboardButton("⏭️ سكيب الملاحظة", callback_data="cust_note_skip_btn")],
        [
            InlineKeyboardButton("↩ رجوع لتعديل السعر", callback_data="cust_txn_back_amount"),
            InlineKeyboardButton("❌ إلغاء وخروج", callback_data="cust_txn_cancel"),
        ],
    ]
    await update.message.reply_text(
        "تم استلام الصورة ✅\n\nالآن أرسل الملاحظة نصاً (أو استخدم سكيب).",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CUST_NOTE


async def cust_note_skip_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سكيب الملاحظة عبر زر بدل كتابة /skip"""
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        cid = context.user_data.get("cust_txn_cid")
        kind = context.user_data.get("cust_txn_kind")
        amount = context.user_data.get("cust_txn_amount")
        photo_file_id = context.user_data.get("cust_txn_photo_file_id")
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not cust:
            await query.edit_message_text("العميل غير موجود.")
            return ConversationHandler.END
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text("غير مسموح.")
            return ConversationHandler.END
        t = CustomerTransaction(
            customer_id=cid,
            amount=amount,
            kind=kind,
            note=None,
            photo_file_id=photo_file_id,
        )
        db.add(t)
        db.commit()
        text, keyboard = await _build_customer_view(db, cust, offset=0)
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
    for k in (
        "cust_txn_kind",
        "cust_txn_cid",
        "cust_txn_amount",
        "cust_txn_note_text",
        "cust_txn_photo_file_id",
    ):
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def cust_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_edit_", ""))
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not cust:
            await query.edit_message_text("العميل غير موجود.")
            return
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text("غير مسموح.")
            return
        text = f"تعديل: {cust.name}\n" + (f"الرقم: {cust.phone}" if cust.phone else "لا يوجد رقم")
        keyboard = [
            [InlineKeyboardButton("تغيير الاسم", callback_data=f"cust_editname_{cid}")],
            [InlineKeyboardButton("تغيير الرقم", callback_data=f"cust_editphone_{cid}")],
            [InlineKeyboardButton("🗑 حذف العميل", callback_data=f"cust_del_{cid}")],
            [InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()


async def cust_edit_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_editname_", ""))
    context.user_data["cust_edit_id"] = cid
    context.user_data["cust_edit_field"] = "name"
    await query.edit_message_text("أرسل الاسم الجديد للعميل:")
    return CUST_EDIT_NAME


async def cust_edit_phone_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_editphone_", ""))
    context.user_data["cust_edit_id"] = cid
    context.user_data["cust_edit_field"] = "phone"
    await query.edit_message_text("أرسل رقم الهاتف الجديد (أو اكتب: حذف لإزالة الرقم):")
    return CUST_EDIT_PHONE


async def cust_edit_name_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("أرسل اسماً صحيحاً.")
        return CUST_EDIT_NAME
    cid = context.user_data.get("cust_edit_id")
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if cust:
            cust.name = name
            db.commit()
        keyboard = [[InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")]]
        await update.message.reply_text("تم تحديث الاسم ✅", reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()
    context.user_data.pop("cust_edit_id", None)
    context.user_data.pop("cust_edit_field", None)
    return ConversationHandler.END


async def cust_edit_phone_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    phone = None if raw.lower() in ("حذف", "delete", "") else normalize_phone(raw)
    if phone is not None and len(phone) < 10:
        await update.message.reply_text("رقم غير صحيح. أرسل الرقم أو اكتب: حذف")
        return CUST_EDIT_PHONE
    cid = context.user_data.get("cust_edit_id")
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if cust:
            cust.phone = phone
            db.commit()
        keyboard = [[InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")]]
        await update.message.reply_text("تم تحديث الرقم ✅" if phone else "تم حذف الرقم ✅", reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()
    context.user_data.pop("cust_edit_id", None)
    context.user_data.pop("cust_edit_field", None)
    return ConversationHandler.END


async def cust_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_del_", ""))
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not cust:
            await query.edit_message_text("العميل غير موجود.")
            return
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text("غير مسموح.")
            return
        name = cust.name
        db.delete(cust)
        db.commit()
        keyboard = [[InlineKeyboardButton("◀ قائمة العملاء", callback_data="menu_customers")]]
        await query.edit_message_text(f"تم حذف العميل: {name} ✅", reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()


async def cust_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مشاركة: رسالة واتساب + رابط لرؤية المعاملات"""
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_share_", ""))
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not cust:
            await query.edit_message_text("العميل غير موجود.")
            return
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text("غير مسموح.")
            return
        bal, gave, took = _balance(cust)
        cur = "د.ع."
        token = secrets.token_urlsafe(16)
        expires = datetime.utcnow() + timedelta(days=30)
        link = ShareLink(customer_id=cust.id, token=token, expires_at=expires)
        db.add(link)
        db.commit()
        # رابط عرض المعاملات:
        # 1) إذا WEB_BASE_URL مضبوط -> رابط موقع
        # 2) fallback -> deep link داخل البوت (حتى لا يتعطل زر المشاركة)
        base = (WEB_BASE_URL or "").strip().rstrip("/")
        if base.startswith("http://") or base.startswith("https://"):
            view_url = f"{base}/creditbook/balance/{token}?lang=ar"
        else:
            me = await context.bot.get_me()
            view_url = f"https://t.me/{me.username}?start=view_{token}"
        if bal > 0:
            msg_balance = f"عليك رصيد {bal:.2f} {cur}"
        elif bal < 0:
            msg_balance = f"لي رصيد {abs(bal):.2f} {cur}"
        else:
            msg_balance = "الرصيد صفر"
        share_text = (
            f"{msg_balance}\n"
            "ــــــــــــــــــــــــ\n"
            f"رؤية جميع المعاملات:\n{view_url}"
        )
        # زر يفتح واتساب على محادثة رقم العميل مع النص جاهز
        # نخلي رابط الصفحة بسطر لوحده حتى واتساب يتعامل معه كرابط تلقائي.
        wa_text = (
            f"{msg_balance}\n\n{view_url}"
            if bal != 0
            else f"الرصيد صفر\n\n{view_url}"
        )
        wa_num = cust.phone and wa_number(cust.phone)
        try:
            if wa_num:
                wa_url = f"https://wa.me/{wa_num}?text={quote(wa_text)}"
                keyboard = [
                    [InlineKeyboardButton("فتح صفحة المعاملات", url=view_url)],
                    [InlineKeyboardButton("فتح واتساب وإرسال الرسالة", url=wa_url)],
                    [InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")],
                ]
            else:
                keyboard = [
                    [InlineKeyboardButton("فتح صفحة المعاملات", url=view_url)],
                    [InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")],
                ]
            await _safe_edit_callback_text(
                query,
                "مشاركة 📤\n\nانسخ النص أدناه أو استخدم الزر لفتح واتساب:\n\n" + share_text,
                keyboard,
            )
        except Exception:
            # fallback أخير: بدون URL buttons حتى لا يتعطل الزر نهائياً
            keyboard = [[InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")]]
            await _safe_edit_callback_text(
                query,
                "مشاركة 📤\n\nانسخ الرابط يدويًا:\n\n" + share_text,
                keyboard,
            )
    finally:
        db.close()


async def cust_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """توجيه callback: عمليات العملاء والمعاملات"""
    query = update.callback_query
    data = query.data
    if data == "cust_add" or data == "noop":
        await query.answer()
        return

    # --- أصناف الصنف ---
    if data.startswith("cust_categories_menu_"):
        try:
            back_cid = int(data.replace("cust_categories_menu_", ""))
        except ValueError:
            await query.answer()
            return
        await query.answer()
        await menu_customer_categories(update, context, back_cid)
        return
    if data.startswith("cust_cat_del_req_"):
        await cust_cat_del_req_click(update, context)
        return
    if data.startswith("cust_cat_del_do_"):
        await cust_cat_del_do_click(update, context)
        return
    if data == "cust_cat_add":
        # سيتم التقاطها بواسطة ConversationHandler
        return

    # --- معاملات ---
    if data.startswith("cust_tx_more_"):
        try:
            rest = data.replace("cust_tx_more_", "", 1)
            cust_id_str, offset_str = rest.split("_", 1)
            await query.answer()
            await customer_detail(update, context, int(cust_id_str), offset=int(offset_str))
        except Exception:
            await query.answer()
        return
    if data.startswith("cust_tx_delete_req_"):
        await cust_tx_delete_req_click(update, context)
        return
    if data.startswith("cust_tx_delete_do_") or data.startswith("cust_tx_delete_"):
        await cust_tx_delete_click(update, context)
        return
    if data.startswith("cust_tx_toggle_kind_"):
        await cust_tx_toggle_kind_click(update, context)
        return
    if data.startswith("cust_tx_") and not data.startswith("cust_tx_edit_"):
        try:
            tx_id = int(data.replace("cust_tx_", ""))
            await cust_tx_detail(update, context, tx_id)
        except ValueError:
            await query.answer()
        return
    if data.startswith("cust_tx_edit_"):
        # سيتم التعامل معها عبر ConversationHandler داخل main.py
        return

    if data.startswith("cust_edit_"):
        await cust_edit_menu(update, context)
        return
    if data.startswith("cust_del_"):
        await cust_delete(update, context)
        return
    if data.startswith("cust_share_"):
        await cust_share(update, context)
        return
    if data.startswith("cust_took_") or data.startswith("cust_gave_"):
        return
    if data.startswith("cust_editname_") or data.startswith("cust_editphone_"):
        return
    if data.startswith("cust_"):
        try:
            cid = int(data.replace("cust_", ""))
            await customer_detail(update, context, cid)
        except ValueError:
            pass
