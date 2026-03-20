# -*- coding: utf-8 -*-
"""تذكيرات تسديد لعملاء دفتر الديون + إشعار الطرفين عند الربط."""
import logging
from datetime import date, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from app_models import User, Customer
from app_models.partner import CustomerPaymentReminder, PartnerLink
from database import SessionLocal

logger = logging.getLogger(__name__)

(REMIND_DUE_DATE, REMIND_OFFSET) = range(300, 302)


def reminder_telegram_chat_ids(db, customer_id: int) -> list[int]:
    """تليجرام chat_id لصاحب العميل + الطرف المربوط إن وُجد."""
    cust = db.query(Customer).filter(Customer.id == customer_id).first()
    if not cust:
        return []
    seen = set()
    out = []

    def add_uid(uid: int):
        u = db.query(User).filter(User.id == uid).first()
        if u and u.telegram_id:
            tid = int(u.telegram_id)
            if tid not in seen:
                seen.add(tid)
                out.append(tid)

    add_uid(cust.user_id)
    link = (
        db.query(PartnerLink)
        .filter(
            PartnerLink.status == "accepted",
            (PartnerLink.inviter_customer_id == customer_id)
            | (PartnerLink.invitee_customer_id == customer_id),
        )
        .first()
    )
    if link:
        add_uid(link.inviter_user_id)
        add_uid(link.invitee_user_id)
    return out


async def cust_reminder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        cid = int((query.data or "").replace("cust_reminder_", ""))
    except ValueError:
        return ConversationHandler.END
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.id == cid).first()
        user = db.query(User).filter(User.telegram_id == update.effective_user.id).first()
        if not cust or not user or cust.user_id != user.id:
            await query.edit_message_text("غير مسموح.")
            return ConversationHandler.END
    finally:
        db.close()
    context.user_data["reminder_cid"] = cid
    from handlers.datetime_picker import start_reminder_datetime_pick

    return await start_reminder_datetime_pick(update, context, cid)


async def show_reminder_offset_after_datetime(
    update: Update, context: ContextTypes.DEFAULT_TYPE, cid: int, due_dt: datetime
) -> int:
    """بعد اختيار التاريخ والوقت من لوحة الأزرار — اختيار نطاق التذكير."""
    query = update.callback_query
    context.user_data["reminder_cid"] = cid
    context.user_data["reminder_due_dt"] = due_dt
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("قبل 5 أيام", callback_data=f"remind_off_{cid}_5"),
                InlineKeyboardButton("قبل 4 أيام", callback_data=f"remind_off_{cid}_4"),
            ],
            [
                InlineKeyboardButton("قبل 3 أيام", callback_data=f"remind_off_{cid}_3"),
                InlineKeyboardButton("قبل يومين", callback_data=f"remind_off_{cid}_2"),
            ],
            [
                InlineKeyboardButton("قبل يوم", callback_data=f"remind_off_{cid}_1"),
                InlineKeyboardButton("يوم الاستحقاق", callback_data=f"remind_off_{cid}_0"),
            ],
            [InlineKeyboardButton("◀ رجوع", callback_data="reminder_flow_back")],
        ]
    )
    when_str = due_dt.strftime("%Y-%m-%d %H:%M")
    await query.edit_message_text(
        f"⏰ وقت الاستحقاق: {when_str}\n\n"
        "متى تريد أن أبدأ بتذكيرك؟ (تذكير يومي منذ ذلك اليوم حتى موعد الاستحقاق)",
        reply_markup=kb,
    )
    return REMIND_OFFSET


async def cust_reminder_offset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if not data.startswith("remind_off_"):
        return ConversationHandler.END
    try:
        rest = data.replace("remind_off_", "", 1)
        cid_str, days_str = rest.rsplit("_", 1)
        cid = int(cid_str)
        days_before = int(days_str)
    except Exception:
        await query.edit_message_text("خطأ في البيانات.")
        return ConversationHandler.END
    if days_before not in range(0, 6):
        await query.edit_message_text("خيار غير صالح.")
        return ConversationHandler.END
    due_dt = context.user_data.get("reminder_due_dt")
    if not due_dt or context.user_data.get("reminder_cid") != cid:
        await query.edit_message_text("انتهت الجلسة. أعد المحاولة من تعديل العميل.")
        return ConversationHandler.END
    due_date = due_dt.date() if isinstance(due_dt, datetime) else due_dt
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.id == cid).first()
        user = db.query(User).filter(User.telegram_id == update.effective_user.id).first()
        if not cust or not user or cust.user_id != user.id:
            await query.edit_message_text("غير مسموح.")
            return ConversationHandler.END
        old = db.query(CustomerPaymentReminder).filter(CustomerPaymentReminder.customer_id == cid).first()
        if old:
            db.delete(old)
            db.flush()
        db.add(
            CustomerPaymentReminder(
                customer_id=cid,
                user_id=user.id,
                due_date=due_date,
                due_at=due_dt if isinstance(due_dt, datetime) else None,
                remind_before_days=days_before,
                last_notified_at=None,
            )
        )
        db.commit()
        label = (
            "يوم الاستحقاق فقط"
            if days_before == 0
            else f"منذ {days_before} يوم قبل الاستحقاق وحتى يومه"
        )
        when_str = (
            due_dt.strftime("%Y-%m-%d %H:%M")
            if isinstance(due_dt, datetime)
            else str(due_date)
        )
        nav_kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("◀ العميل", callback_data=f"cust_{cid}"),
                    InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu"),
                ],
            ]
        )
        await query.edit_message_text(
            f"✅ تم حفظ التذكير.\n\n"
            f"العميل: {cust.name}\n"
            f"الاستحقاق: {when_str}\n"
            f"النطاق: {label}\n\n"
            "سنرسل تذكيراً مرة واحدة يومياً خلال هذه الفترة.",
            reply_markup=nav_kb,
        )
        extra = (
            db.query(PartnerLink)
            .filter(
                PartnerLink.status == "accepted",
                (PartnerLink.inviter_customer_id == cid) | (PartnerLink.invitee_customer_id == cid),
            )
            .first()
        )
        if extra:
            targets = reminder_telegram_chat_ids(db, cid)
            me_tid = int(user.telegram_id)
            note = (
                f"🔔 تم ضبط تذكير تسديد للعميل «{cust.name}» — "
                f"الاستحقاق {when_str} ({label})."
            )
            for chat_id in targets:
                if chat_id != me_tid:
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=note)
                    except Exception as e:
                        logger.warning("reminder partner notify: %s", e)
    except Exception as e:
        logger.exception("cust_reminder_offset: %s", e)
        db.rollback()
        await query.edit_message_text("تعذر حفظ التذكير.")
    finally:
        db.close()
    context.user_data.pop("reminder_cid", None)
    context.user_data.pop("reminder_due_dt", None)
    return ConversationHandler.END


async def cust_reminder_back_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إنهاء خطوة التذكير والعودة (زر رجوع)."""
    from handlers.datetime_picker import clear_dt_user_data

    query = update.callback_query
    if query:
        await query.answer()
        clear_dt_user_data(context)
        context.user_data.pop("reminder_cid", None)
        context.user_data.pop("reminder_due_dt", None)
        await query.edit_message_text("تم الرجوع.")
    else:
        clear_dt_user_data(context)
        context.user_data.pop("reminder_cid", None)
        context.user_data.pop("reminder_due_dt", None)
        await update.message.reply_text("تم الرجوع.")
    return ConversationHandler.END


async def reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """يومياً: تذكير خلال نافذة [due - remind_before_days, due]."""
    db = SessionLocal()
    try:
        today = date.today()
        now = datetime.utcnow()
        rows = db.query(CustomerPaymentReminder).all()
        for r in rows:
            if r.due_at is not None:
                due_d = r.due_at.date()
            else:
                due = r.due_date
                due_d = due.date() if hasattr(due, "date") else due
            start = due_d - timedelta(days=int(r.remind_before_days or 0))
            if today < start or today > due_d:
                continue
            if r.last_notified_at and r.last_notified_at.date() == today:
                continue
            cust = db.query(Customer).filter(Customer.id == r.customer_id).first()
            if not cust:
                continue
            days_left = (due_d - today).days
            when_line = (
                r.due_at.strftime("%Y-%m-%d %H:%M") if r.due_at else due_d.isoformat()
            )
            text = (
                f"🔔 تذكير تسديد\n\n"
                f"العميل: {cust.name}\n"
                f"موعد الاستحقاق: {when_line}\n"
                f"{'اليوم هو الاستحقاق!' if days_left == 0 else f'متبقي {days_left} يوم.'}"
            )
            bot = context.application.bot
            for chat_id in reminder_telegram_chat_ids(db, r.customer_id):
                try:
                    await bot.send_message(chat_id=chat_id, text=text)
                except Exception as e:
                    logger.warning("reminder_job send %s: %s", chat_id, e)
            r.last_notified_at = now
        db.commit()
    except Exception as e:
        logger.exception("reminder_job: %s", e)
        db.rollback()
    finally:
        db.close()
