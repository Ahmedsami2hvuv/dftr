# -*- coding: utf-8 -*-
"""دفتر الديون: عملاء، أخذت/أعطيت، مشاركة"""
import secrets
from urllib.parse import quote
from decimal import Decimal
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import SessionLocal
from app_models import User, Customer, CustomerTransaction, ShareLink
from utils.phone import normalize_phone, wa_number

(
    CUST_NAME,
    CUST_PHONE,
    CUST_AMOUNT,
    CUST_NOTE,
    CUST_EDIT_NAME,
    CUST_EDIT_PHONE,
) = range(6)


def get_current_user(db, telegram_id: int):
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def _balance(customer):
    gave = sum(t.amount for t in customer.transactions if t.kind == "gave")
    took = sum(t.amount for t in customer.transactions if t.kind == "took")
    return float(gave - took), float(gave), float(took)


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
            label = f"{'🔴' if bal > 0 else '🟢'} {c.name}" + (f" — {c.phone}" if c.phone else "")
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
        "تم ✅\n\nأرسل رقم هاتف العميل (اختياري — للتخطي اكتب: تخطى أو /skip):"
    )
    return CUST_PHONE


async def cust_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    if raw in ("تخطى", "skip", "/skip", ""):
        phone = None
    else:
        phone = normalize_phone(raw)
        if len(phone) < 10:
            await update.message.reply_text("رقم غير صحيح. أرسل الرقم أو اكتب تخطى.")
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


async def customer_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, customer_id: int):
    """عرض تفاصيل عميل: الرصيد + أزرار أخذت / أعطيت / تعديل / مشاركة"""
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
        bal, gave, took = _balance(cust)
        cur = "د.ع."
        if bal > 0:
            balance_text = f"الرصيد الحالي: {bal:.2f} {cur} (العميل مدين لك)"
        elif bal < 0:
            balance_text = f"الرصيد الحالي: {abs(bal):.2f} {cur} (أنت مدين للعميل)"
        else:
            balance_text = "الرصيد الحالي: 0 (لا دين)"
        text = (
            f"📒 {cust.name}\n"
            + (f"📞 {cust.phone}\n" if cust.phone else "")
            + f"\n{balance_text}\n"
            + f"أعطيت (مدين لك): {gave:.2f} {cur}\n"
            + f"أخذت (دفع): {took:.2f} {cur}"
        )
        keyboard = [
            [
                InlineKeyboardButton("🔴 أخذت", callback_data=f"cust_took_{cust.id}"),
                InlineKeyboardButton("🟢 أعطيت", callback_data=f"cust_gave_{cust.id}"),
            ],
            [InlineKeyboardButton("✏️ تعديل معلومات العميل", callback_data=f"cust_edit_{cust.id}")],
            [InlineKeyboardButton("📤 مشاركة", callback_data=f"cust_share_{cust.id}")],
            [InlineKeyboardButton("◀ قائمة العملاء", callback_data="menu_customers")],
        ]
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def cust_took(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أخذت (باللون الأحمر) — العميل دفع"""
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_took_", ""))
    context.user_data["cust_txn_kind"] = "took"
    context.user_data["cust_txn_cid"] = cid
    await query.edit_message_text("أخذت 🔴\n\nأرسل المبلغ (رقم):")
    return CUST_AMOUNT


async def cust_gave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أعطيت (باللون الأخضر) — العميل مدين"""
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_gave_", ""))
    context.user_data["cust_txn_kind"] = "gave"
    context.user_data["cust_txn_cid"] = cid
    await query.edit_message_text("أعطيت 🟢\n\nأرسل المبلغ (رقم):")
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
    await update.message.reply_text("اختياري: اكتب ملاحظة أو /skip للتخطي:")
    return CUST_NOTE


async def cust_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note = (update.message.text or "").strip()
    if note in ("/skip", "تخطى", "skip"):
        note = None
    db = SessionLocal()
    try:
        cid = context.user_data.get("cust_txn_cid")
        kind = context.user_data.get("cust_txn_kind")
        amount = context.user_data.get("cust_txn_amount")
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not cust:
            await update.message.reply_text("العميل غير موجود.")
            return ConversationHandler.END
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await update.message.reply_text("غير مسموح.")
            return ConversationHandler.END
        t = CustomerTransaction(customer_id=cid, amount=amount, kind=kind, note=note or None)
        db.add(t)
        db.commit()
        kind_ar = "أخذت" if kind == "took" else "أعطيت"
        keyboard = [
            [InlineKeyboardButton("عرض العميل", callback_data=f"cust_{cid}")],
            [InlineKeyboardButton("◀ قائمة العملاء", callback_data="menu_customers")],
        ]
        await update.message.reply_text(
            f"تم تسجيل {kind_ar} ✅ {amount} د.ع.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
    for k in ("cust_txn_kind", "cust_txn_cid", "cust_txn_amount"):
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
        bot_username = (await context.bot.get_me()).username
        view_url = f"https://t.me/{bot_username}?start=view_{token}"
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
        wa_text = f"عليك رصيد {bal:.2f} {cur}\nرؤية جميع المعاملات: {view_url}" if bal >= 0 else f"لي رصيد {abs(bal):.2f} {cur}\nرؤية جميع المعاملات: {view_url}"
        wa_num = cust.phone and wa_number(cust.phone)
        if wa_num:
            wa_url = f"https://wa.me/{wa_num}?text={quote(wa_text)}"
            keyboard = [
                [InlineKeyboardButton("فتح واتساب وإرسال الرسالة", url=wa_url)],
                [InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")],
            ]
        else:
            keyboard = [[InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")]]
        await query.edit_message_text(
            "مشاركة 📤\n\nانسخ النص أدناه أو استخدم الزر لفتح واتساب:\n\n" + share_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def cust_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """توجيه callback: cust_ID أو cust_took_ID أو cust_gave_ID أو cust_edit_ID أو cust_share_ID"""
    query = update.callback_query
    data = query.data
    if data == "cust_add":
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
