# -*- coding: utf-8 -*-
"""لوحة الأدمن"""
import io
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from database import SessionLocal
from app_models import (
    BRAND_LOGO_SETTING_KEY,
    User,
    LedgerEntry,
    Debt,
    Customer,
    CustomerTransaction,
    FeedbackMessage,
    SiteSetting,
)
from config import ADMIN_ID, BOT_USERNAME

ADMIN_BROADCAST_CONTENT = 900
ADMIN_BROADCAST_BUTTONS = 901
ADMIN_FEEDBACK_SEARCH = 902
ADMIN_BRAND_LOGO = 904

MAX_BRAND_LOGO_BYTES = 2_500_000

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def _broadcast_buttons_summary(selected: set[str]) -> str:
    names = []
    if "start" in selected:
        names.append("ستارت")
    if "update" in selected:
        names.append("تحديث")
    if "comment" in selected:
        names.append("تعليق")
    if "suggest" in selected:
        names.append("مشكلة/اقتراح")
    return "، ".join(names) if names else "بدون أزرار إضافية"


def _broadcast_keyboard(selected: set[str]):
    row = []
    if "start" in selected:
        row.append(InlineKeyboardButton("🚀 ستارت", callback_data="bc_start"))
    if "update" in selected:
        row.append(InlineKeyboardButton("🔄 تحديث", callback_data="bc_update"))
    if row:
        rows = [row]
    else:
        rows = []
    row2 = []
    if "comment" in selected:
        row2.append(InlineKeyboardButton("💬 تعليق", callback_data="bc_comment"))
    if "suggest" in selected:
        row2.append(InlineKeyboardButton("💡 مشكلة/اقتراح", callback_data="bc_suggest"))
    if row2:
        rows.append(row2)
    return InlineKeyboardMarkup(rows) if rows else None


def _extract_msg_payload(msg):
    if msg.text:
        return "text", msg.text, None
    if msg.photo:
        return "photo", msg.caption or "", msg.photo[-1].file_id
    if msg.video:
        return "video", msg.caption or "", msg.video.file_id
    if msg.voice:
        return "voice", msg.caption or "", msg.voice.file_id
    if msg.audio:
        return "audio", msg.caption or "", msg.audio.file_id
    if msg.document:
        return "document", msg.caption or "", msg.document.file_id
    if msg.sticker:
        return "sticker", "", msg.sticker.file_id
    return "unknown", "", None


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
        feedback_count = db.query(FeedbackMessage).count()
        text = (
            "لوحة الأدمن 🔐\n\n"
            f"عدد المستخدمين: {users_count}\n"
            f"عدد قيود الدفتر: {entries_count}\n"
            f"عدد سجلات الديون: {debts_count}\n"
            f"المشاكل/الاقتراحات: {feedback_count}\n"
        )
        keyboard = [
            [InlineKeyboardButton("👥 قائمة المستخدمين", callback_data="admin_users")],
            [InlineKeyboardButton("📢 بث / إذاعة", callback_data="admin_broadcast")],
            [InlineKeyboardButton("🖼️ تغيير شعار الموقع", callback_data="admin_brand_logo")],
            [InlineKeyboardButton("📥 المشاكل والاقتراحات", callback_data="admin_feedbacks")],
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
            lines.append(f"• {name} | {u.phone or '—'} | {tg}")
            keyboard.append([InlineKeyboardButton(f"👤 {name}"[:64], callback_data=f"admin_user_{u.id}")])
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
            tx_count = db.query(CustomerTransaction).filter(CustomerTransaction.customer_id.in_(customer_ids)).count()

        name = u.full_name or u.username or "—"
        username_line = f"يوزر: @{u.username}\n" if u.username else "يوزر: —\n"
        text = "ملف المستخدم 👤\n\n" + f"الاسم: {name}\n" + username_line
        text += (
            f"الهاتف: {u.phone or '—'}\n"
            f"معرّف Telegram: {u.telegram_id or '—'}\n"
            f"تاريخ التسجيل: {u.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"دفتر الحسابات:\n- الدخل: {total_income:.2f}\n- المصروف: {total_expense:.2f}\n\n"
            f"دفتر الديون:\n- له على الآخرين: {they_owe:.2f}\n- عليه للآخرين: {i_owe:.2f}\n"
            f"- عدد العملاء: {len(customers)}\n- عدد معاملات العملاء: {tx_count}"
        )

        keyboard = []
        if u.telegram_id:
            keyboard.append([InlineKeyboardButton("💬 مراسلة المستخدم على تليجرام", url=f"tg://user?id={u.telegram_id}")])
        keyboard.append([InlineKeyboardButton("◀ قائمة المستخدمين", callback_data="admin_users")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()


async def admin_feedbacks_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    data = query.data or "admin_feedbacks"
    # filters: all / open / done
    if data == "admin_feedbacks_open":
        filter_mode = "open"
    elif data == "admin_feedbacks_done":
        filter_mode = "done"
    else:
        filter_mode = "all"

    db = SessionLocal()
    try:
        q = db.query(FeedbackMessage)
        if filter_mode == "open":
            q = q.filter(FeedbackMessage.is_resolved == 0)
        elif filter_mode == "done":
            q = q.filter(FeedbackMessage.is_resolved == 1)
        items = q.order_by(FeedbackMessage.created_at.desc()).limit(30).all()
        keyboard = []
        title_map = {"all": "الكل", "open": "المفتوحة", "done": "المعالجة"}
        lines = [f"المشاكل والاقتراحات ({title_map[filter_mode]} - آخر 30):"]
        for f in items:
            title = (f.user_name or "مستخدم")[:24]
            st = "✅" if int(f.is_resolved or 0) == 1 else "🟡"
            lines.append(f"• {st} {title} | {f.content_type} | {f.created_at.strftime('%Y-%m-%d %H:%M')}")
            keyboard.append(
                [InlineKeyboardButton(f"{st} {title}"[:64], callback_data=f"admin_feedback_{f.id}")]
            )
        keyboard.append(
            [
                InlineKeyboardButton("الكل", callback_data="admin_feedbacks"),
                InlineKeyboardButton("المفتوحة", callback_data="admin_feedbacks_open"),
                InlineKeyboardButton("المعالجة", callback_data="admin_feedbacks_done"),
            ]
        )
        keyboard.append([InlineKeyboardButton("🔎 بحث", callback_data="admin_feedback_search")])
        keyboard.append([InlineKeyboardButton("◀ لوحة الأدمن", callback_data="admin_panel")])
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()


async def admin_feedback_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    fid = int(query.data.replace("admin_feedback_", ""))
    db = SessionLocal()
    try:
        f = db.query(FeedbackMessage).filter(FeedbackMessage.id == fid).first()
        if not f:
            await query.edit_message_text("العنصر غير موجود.")
            return
        text = (
            "تفاصيل الرسالة 📩\n\n"
            f"الاسم: {f.user_name or '—'}\n"
            f"الهاتف: {f.user_phone or '—'}\n"
            f"Telegram ID: {f.user_telegram_id or '—'}\n"
            f"المصدر: {f.source or '—'}\n"
            f"النوع: {f.content_type}\n"
            f"الحالة: {'✅ تمت المعالجة' if int(f.is_resolved or 0) == 1 else '🟡 مفتوحة'}\n"
            f"التاريخ: {f.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"النص:\n{f.text or '—'}"
        )
        toggle_label = "↩ إعادة فتح" if int(f.is_resolved or 0) == 1 else "✅ تمت المعالجة"
        kb = [
            [InlineKeyboardButton(toggle_label, callback_data=f"admin_feedback_toggle_{f.id}")],
            [InlineKeyboardButton("◀ المشاكل والاقتراحات", callback_data="admin_feedbacks")],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    finally:
        db.close()


async def admin_feedback_toggle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    fid = int(query.data.replace("admin_feedback_toggle_", ""))
    db = SessionLocal()
    try:
        f = db.query(FeedbackMessage).filter(FeedbackMessage.id == fid).first()
        if not f:
            await query.edit_message_text("العنصر غير موجود.")
            return
        f.is_resolved = 0 if int(f.is_resolved or 0) == 1 else 1
        db.commit()
    finally:
        db.close()
    # ارجع لعرض التفاصيل بعد التبديل
    await admin_feedback_detail(update, context)


async def admin_feedback_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton("❌ إلغاء ورجوع", callback_data="admin_feedback_search_cancel")]]
    await query.edit_message_text(
        "ابحث باسم المستخدم أو رقمه أو نص من الرسالة:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ADMIN_FEEDBACK_SEARCH


async def admin_feedback_search_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    qtext = (update.message.text or "").strip()
    if not qtext:
        await update.message.reply_text("اكتب كلمة بحث صحيحة.")
        return ADMIN_FEEDBACK_SEARCH
    db = SessionLocal()
    try:
        items = (
            db.query(FeedbackMessage)
            .filter(
                (FeedbackMessage.user_name.ilike(f"%{qtext}%"))
                | (FeedbackMessage.user_phone.ilike(f"%{qtext}%"))
                | (FeedbackMessage.text.ilike(f"%{qtext}%"))
            )
            .order_by(FeedbackMessage.created_at.desc())
            .limit(30)
            .all()
        )
        lines = [f"نتائج البحث: {qtext}"]
        keyboard = []
        for f in items:
            st = "✅" if int(f.is_resolved or 0) == 1 else "🟡"
            title = (f.user_name or "مستخدم")[:24]
            lines.append(f"• {st} {title} | {f.content_type} | {f.created_at.strftime('%Y-%m-%d %H:%M')}")
            keyboard.append([InlineKeyboardButton(f"{st} {title}"[:64], callback_data=f"admin_feedback_{f.id}")])
        keyboard.append([InlineKeyboardButton("◀ المشاكل والاقتراحات", callback_data="admin_feedbacks")])
        await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()
    return ConversationHandler.END


async def admin_feedback_search_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "تم الإلغاء ✅",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ المشاكل والاقتراحات", callback_data="admin_feedbacks")]]
        ),
    )
    return ConversationHandler.END


async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data["admin_bc_payload"] = None
    context.user_data["admin_bc_buttons"] = {"update"}
    keyboard = [[InlineKeyboardButton("❌ إلغاء ورجوع", callback_data="admin_broadcast_cancel")]]
    await query.edit_message_text(
        "إذاعة / بث 📢\n\n"
        "أرسل الآن محتوى البث: نص، صورة، فيديو، صوت، ملف أو أي وسائط.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ADMIN_BROADCAST_CONTENT


async def admin_broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("admin_bc_payload", None)
    context.user_data.pop("admin_bc_buttons", None)
    await query.edit_message_text(
        "تم الإلغاء ✅",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀ لوحة الأدمن", callback_data="admin_panel")]]),
    )
    return ConversationHandler.END


async def admin_broadcast_receive_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    payload = _extract_msg_payload(update.message)
    context.user_data["admin_bc_payload"] = payload
    selected = context.user_data.get("admin_bc_buttons", {"update"})
    keyboard = [
        [
            InlineKeyboardButton(f"{'✅' if 'start' in selected else '☑️'} ستارت", callback_data="admin_bc_toggle_start"),
            InlineKeyboardButton(f"{'✅' if 'update' in selected else '☑️'} تحديث", callback_data="admin_bc_toggle_update"),
        ],
        [
            InlineKeyboardButton(f"{'✅' if 'comment' in selected else '☑️'} تعليق", callback_data="admin_bc_toggle_comment"),
            InlineKeyboardButton(f"{'✅' if 'suggest' in selected else '☑️'} مشكلة/اقتراح", callback_data="admin_bc_toggle_suggest"),
        ],
        [
            InlineKeyboardButton("✅ إرسال البث", callback_data="admin_bc_send"),
            InlineKeyboardButton("❌ إلغاء ورجوع", callback_data="admin_broadcast_cancel"),
        ],
    ]
    await update.message.reply_text(
        "تم استلام محتوى البث ✅\n"
        f"الأزرار المختارة: {_broadcast_buttons_summary(selected)}\n\n"
        "يمكنك تفعيل/إلغاء الأزرار ثم اضغط (إرسال البث).",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ADMIN_BROADCAST_BUTTONS


async def admin_broadcast_toggle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected = context.user_data.get("admin_bc_buttons", set())
    data = query.data
    mapping = {
        "admin_bc_toggle_start": "start",
        "admin_bc_toggle_update": "update",
        "admin_bc_toggle_comment": "comment",
        "admin_bc_toggle_suggest": "suggest",
    }
    key = mapping.get(data)
    if key:
        if key in selected:
            selected.remove(key)
        else:
            selected.add(key)
    context.user_data["admin_bc_buttons"] = selected
    keyboard = [
        [
            InlineKeyboardButton(f"{'✅' if 'start' in selected else '☑️'} ستارت", callback_data="admin_bc_toggle_start"),
            InlineKeyboardButton(f"{'✅' if 'update' in selected else '☑️'} تحديث", callback_data="admin_bc_toggle_update"),
        ],
        [
            InlineKeyboardButton(f"{'✅' if 'comment' in selected else '☑️'} تعليق", callback_data="admin_bc_toggle_comment"),
            InlineKeyboardButton(f"{'✅' if 'suggest' in selected else '☑️'} مشكلة/اقتراح", callback_data="admin_bc_toggle_suggest"),
        ],
        [
            InlineKeyboardButton("✅ إرسال البث", callback_data="admin_bc_send"),
            InlineKeyboardButton("❌ إلغاء ورجوع", callback_data="admin_broadcast_cancel"),
        ],
    ]
    await query.edit_message_text(
        f"الأزرار المختارة: {_broadcast_buttons_summary(selected)}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ADMIN_BROADCAST_BUTTONS


async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    payload = context.user_data.get("admin_bc_payload")
    selected = context.user_data.get("admin_bc_buttons", set())
    if not payload:
        await query.edit_message_text("لا يوجد محتوى للبث. ابدأ من جديد.")
        return ConversationHandler.END

    content_type, text, file_id = payload
    kb = _broadcast_keyboard(selected)

    db = SessionLocal()
    try:
        users = db.query(User).filter(User.telegram_id.isnot(None)).all()
    finally:
        db.close()

    sent = 0
    failed = 0
    for u in users:
        try:
            if content_type == "text":
                await context.bot.send_message(chat_id=u.telegram_id, text=text or " ")
                if kb:
                    await context.bot.send_message(chat_id=u.telegram_id, text="خيارات:", reply_markup=kb)
            elif content_type == "photo" and file_id:
                await context.bot.send_photo(chat_id=u.telegram_id, photo=file_id, caption=(text or None), reply_markup=kb)
            elif content_type == "video" and file_id:
                await context.bot.send_video(chat_id=u.telegram_id, video=file_id, caption=(text or None), reply_markup=kb)
            elif content_type == "voice" and file_id:
                await context.bot.send_voice(chat_id=u.telegram_id, voice=file_id, caption=(text or None), reply_markup=kb)
            elif content_type == "audio" and file_id:
                await context.bot.send_audio(chat_id=u.telegram_id, audio=file_id, caption=(text or None), reply_markup=kb)
            elif content_type == "document" and file_id:
                await context.bot.send_document(chat_id=u.telegram_id, document=file_id, caption=(text or None), reply_markup=kb)
            elif content_type == "sticker" and file_id:
                await context.bot.send_sticker(chat_id=u.telegram_id, sticker=file_id)
                if text or kb:
                    await context.bot.send_message(chat_id=u.telegram_id, text=(text or " "), reply_markup=kb)
            else:
                await context.bot.send_message(chat_id=u.telegram_id, text=(text or " "))
                if kb:
                    await context.bot.send_message(chat_id=u.telegram_id, text="خيارات:", reply_markup=kb)
            sent += 1
        except Exception:
            failed += 1

    context.user_data.pop("admin_bc_payload", None)
    context.user_data.pop("admin_bc_buttons", None)
    result_kb = [
        [
            InlineKeyboardButton("📢 بث جديد", callback_data="admin_broadcast"),
            InlineKeyboardButton("◀ خروج", callback_data="admin_panel"),
        ]
    ]
    await query.edit_message_text(
        f"تم الإرسال ✅\n\nنجح: {sent}\nفشل: {failed}",
        reply_markup=InlineKeyboardMarkup(result_kb),
    )
    return ConversationHandler.END


async def bc_start_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if BOT_USERNAME:
        kb = [[InlineKeyboardButton("🚀 فتح البوت", url=f"https://t.me/{BOT_USERNAME}?start=app")]]
        await query.message.reply_text("اضغط لفتح البوت:", reply_markup=InlineKeyboardMarkup(kb))


async def bc_update_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if BOT_USERNAME:
        kb = [[InlineKeyboardButton("🔄 تحديث", url=f"https://t.me/{BOT_USERNAME}?start=update")]]
        await query.message.reply_text("تم تحديث البوت. اضغط زر التحديث:", reply_markup=InlineKeyboardMarkup(kb))


# --- شعار صفحة المشاركة (يُخزَّن في PostgreSQL على Railway) ---


async def admin_brand_logo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    await query.edit_message_text(
        "🖼️ تغيير شعار الموقع\n\n"
        "تُحفظ الصورة في قاعدة البيانات وتظهر بجانب «دفتر الديون» في صفحة روابط المشاركة فوراً.\n\n"
        f"أرسل صورة (أو ملف صورة). الحد الأقصى ≈ {MAX_BRAND_LOGO_BYTES // 1_000_000} ميجا.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ إلغاء", callback_data="admin_brand_logo_cancel")]]
        ),
    )
    return ADMIN_BRAND_LOGO


async def admin_brand_logo_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    await update.message.reply_text(
        "تم الإلغاء.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀ لوحة الأدمن", callback_data="admin_panel")]]),
    )
    return ConversationHandler.END


async def admin_brand_logo_on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قبول صورة كصورة أو كملف صورة — بدون الاعتماد على Document.IMAGE (توافق أوسع)."""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    msg = update.message
    file_id = None
    size_hint = 0
    if msg.photo:
        p = msg.photo[-1]
        file_id = p.file_id
        size_hint = p.file_size or 0
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
        file_id = msg.document.file_id
        size_hint = msg.document.file_size or 0
    else:
        await msg.reply_text(
            "أرسل صورة كصورة (مرفق صورة من المعرض)، أو ملف JPG/PNG/WebP.\n"
            "تجنّب إرسال ملفات غير صور."
        )
        return ADMIN_BRAND_LOGO

    if size_hint and size_hint > MAX_BRAND_LOGO_BYTES:
        await msg.reply_text("الملف كبير جداً. أرسل صورة أصغر.")
        return ADMIN_BRAND_LOGO

    try:
        tg_file = await context.bot.get_file(file_id)
        raw = None
        try:
            data = await tg_file.download_as_bytearray()
            raw = bytes(data)
        except Exception:
            buf = io.BytesIO()
            await tg_file.download_to_memory(buf)
            raw = buf.getvalue()
    except Exception as e:
        logger.exception("تنزيل شعار من تليجرام فشل")
        await msg.reply_text(f"تعذّر تنزيل الملف من تليجرام.\nتفاصيل: {e!s}")
        return ADMIN_BRAND_LOGO

    if not raw or len(raw) > MAX_BRAND_LOGO_BYTES:
        await msg.reply_text("الصورة كبيرة بعد التنزيل أو فارغة. أرسل غيرها.")
        return ADMIN_BRAND_LOGO

    db = SessionLocal()
    try:
        row = db.query(SiteSetting).filter(SiteSetting.key == BRAND_LOGO_SETTING_KEY).first()
        if not row:
            row = SiteSetting(key=BRAND_LOGO_SETTING_KEY, blob_value=raw)
            db.add(row)
        else:
            row.blob_value = raw
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("حفظ الشعار في قاعدة البيانات فشل")
        await msg.reply_text(
            f"❌ تعذّر الحفظ في قاعدة البيانات.\n"
            f"تأكد أن الجدول site_settings وُجد (أعد تشغيل البوت بعد التحديث).\n\n"
            f"الخطأ: {e!s}"
        )
        return ConversationHandler.END
    finally:
        db.close()

    keyboard = [[InlineKeyboardButton("◀ لوحة الأدمن", callback_data="admin_panel")]]
    await msg.reply_text(
        "✅ تم حفظ الشعار في قاعدة البيانات.\n"
        "افتح رابط المشاركة وحدّث الصفحة (أو Ctrl+F5) لرؤية الشعار الجديد.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ConversationHandler.END


async def admin_brand_logo_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    await query.edit_message_text(
        "تم الإلغاء.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀ لوحة الأدمن", callback_data="admin_panel")]]),
    )
    return ConversationHandler.END
