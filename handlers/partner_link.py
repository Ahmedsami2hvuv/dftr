# -*- coding: utf-8 -*-
"""ربط مستخدمين لعميل واحد + إرسال معاملات للموافقة (معكوسة)."""
import logging
from decimal import Decimal
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import SessionLocal
from app_models import User, Customer, CustomerTransaction
from app_models.partner import PartnerLink, PartnerPendingTx

logger = logging.getLogger(__name__)


def invert_kind(kind: str) -> str:
    return "took" if kind == "gave" else "gave"


def _get_accepted_link_for_customer(db, customer_id: int) -> PartnerLink | None:
    return (
        db.query(PartnerLink)
        .filter(
            PartnerLink.status == "accepted",
            (PartnerLink.inviter_customer_id == customer_id)
            | (PartnerLink.invitee_customer_id == customer_id),
        )
        .first()
    )


def maybe_queue_partner_tx(db, tx: CustomerTransaction) -> None:
    """بعد حفظ معاملة: إن كان العميل مربوطاً، أضفها لطابور انتظار الطرف الآخر."""
    try:
        link = _get_accepted_link_for_customer(db, tx.customer_id)
        if not link:
            return
        # لا تكرار لنفس المعاملة
        exists = (
            db.query(PartnerPendingTx)
            .filter(PartnerPendingTx.source_tx_id == tx.id)
            .first()
        )
        if exists:
            return
        # لا نصفّ معاملة أُنشئت كنسخة معكوسة (مرجع من pending)
        if (
            db.query(PartnerPendingTx)
            .filter(PartnerPendingTx.mirrored_tx_id == tx.id)
            .first()
        ):
            return
        db.add(
            PartnerPendingTx(
                partner_link_id=link.id,
                source_tx_id=tx.id,
                status="pending",
            )
        )
        db.commit()
    except Exception as e:
        logger.exception("maybe_queue_partner_tx: %s", e)
        db.rollback()


def _other_user_id(link: PartnerLink, customer_id: int) -> int | None:
    if link.inviter_customer_id == customer_id:
        return link.invitee_user_id
    if link.invitee_customer_id == customer_id:
        return link.inviter_user_id
    return None


def _mirror_customer_id(link: PartnerLink, source_customer_id: int) -> int | None:
    if source_customer_id == link.inviter_customer_id:
        return link.invitee_customer_id
    if source_customer_id == link.invitee_customer_id:
        return link.inviter_customer_id
    return None


def _format_tx_line(tx: CustomerTransaction) -> str:
    kind_ar = "أعطيت" if tx.kind == "gave" else "أخذت"
    amt = float(tx.amount or 0)
    note = (tx.note or "").strip()
    dt = tx.created_at.strftime("%Y-%m-%d %H:%M") if tx.created_at else ""
    return f"• {kind_ar} {amt:.2f} د.ع." + (f" — {note[:40]}" if note else "") + (f" ({dt})" if dt else "")


async def partner_link_invite_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يبدأ من زر ربط — ينشئ دعوة ويعرض الرابط."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    try:
        cid = int(data.replace("cust_partner_invite_", ""))
    except Exception:
        return
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == update.effective_user.id).first()
        if not user:
            await query.edit_message_text("سجّل الدخول أولاً.")
            return
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not cust or cust.user_id != user.id:
            await query.edit_message_text("غير مسموح.")
            return
        existing = (
            db.query(PartnerLink)
            .filter(
                PartnerLink.status == "accepted",
                (PartnerLink.inviter_customer_id == cid) | (PartnerLink.invitee_customer_id == cid),
            )
            .first()
        )
        if existing:
            await query.edit_message_text(
                "هذا العميل مربوط مسبقاً مع مستخدم آخر.\n"
                "لإزالة الربط تواصل مع الدعم لاحقاً (قريباً).",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("◀ رجوع", callback_data=f"cust_edit_{cid}")]]
                ),
            )
            return
        pending = (
            db.query(PartnerLink)
            .filter(PartnerLink.inviter_customer_id == cid, PartnerLink.status == "pending")
            .first()
        )
        if pending:
            token = pending.token
        else:
            import secrets

            token = secrets.token_urlsafe(16)
            db.add(
                PartnerLink(
                    token=token,
                    inviter_user_id=user.id,
                    inviter_customer_id=cid,
                    status="pending",
                )
            )
            db.commit()
        me = await context.bot.get_me()
        link_url = f"https://t.me/{me.username}?start=plink_{token}"
        inviter_name = user.full_name or user.username or "مستخدم"
        text = (
            f"🔗 ربط مع مستخدم آخر\n\n"
            f"سيُنشأ عند الطرف الآخر عميل باسم: {inviter_name}\n\n"
            "أرسل الرابط أدناه للمستخدم الآخر (واتساب/تليجرام).\n"
            "عند قبوله يُنشأ عنده عميل باسم المرسل، وتُرسل معاملاتك له للموافقة "
            "(تظهر عنده معكوسة: أعطيتك ← أخذته).\n\n"
            f"{link_url}"
        )
        wa_url = f"https://api.whatsapp.com/send?text={quote(text)}"
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("📤 مشاركة", url=wa_url)],
                    [InlineKeyboardButton("◀ رجوع لتعديل العميل", callback_data=f"cust_edit_{cid}")],
                ]
            ),
        )
    finally:
        db.close()


async def partner_send_updates_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        cid = int((query.data or "").replace("cust_partner_send_", ""))
    except Exception:
        return
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == update.effective_user.id).first()
        if not user:
            await query.edit_message_text("سجّل الدخول أولاً.")
            return
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not cust or cust.user_id != user.id:
            await query.edit_message_text("غير مسموح.")
            return
        link = _get_accepted_link_for_customer(db, cid)
        if not link:
            await query.answer("لا يوجد ربط مفعّل لهذا العميل.", show_alert=True)
            return
        other_uid = _other_user_id(link, cid)
        if not other_uid:
            await query.answer("لم يُكمل الطرف الآخر الربط بعد.", show_alert=True)
            return
        other = db.query(User).filter(User.id == other_uid).first()
        if not other or not other.telegram_id:
            await query.answer("الطرف الآخر ليس لديه تليجرام مربوط.", show_alert=True)
            return
        all_pending = (
            db.query(PartnerPendingTx)
            .filter(PartnerPendingTx.partner_link_id == link.id, PartnerPendingTx.status == "pending")
            .all()
        )
        lines = []
        pids = []
        for p in all_pending:
            tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == p.source_tx_id).first()
            if not tx or tx.customer_id != cid:
                continue
            lines.append(_format_tx_line(tx))
            pids.append(p.id)
        if not pids:
            await query.answer("لا توجد معاملات جديدة بانتظار الإرسال.", show_alert=True)
            return
        if not lines:
            await query.answer("لا توجد معاملات صالحة.", show_alert=True)
            return
        inviter_name = user.full_name or user.username or "مستخدم"
        body = (
            f"📤 {inviter_name} يطلب موافقتك على معاملات:\n\n"
            + "\n".join(lines[:30])
        )
        if len(lines) > 30:
            body += f"\n\n… و {len(lines) - 30} أخرى"
        import secrets

        batch_id = secrets.token_urlsafe(6)[:10]
        context.bot_data.setdefault("partner_batches", {})[batch_id] = {
            "pending_ids": pids,
            "link_id": link.id,
            "sender_user_id": user.id,
        }
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ موافقة على الكل", callback_data=f"ppart_ok_{batch_id}")],
                [InlineKeyboardButton("❌ رفض الكل", callback_data=f"ppart_no_{batch_id}")],
            ]
        )
        try:
            await context.bot.send_message(
                chat_id=int(other.telegram_id),
                text=body,
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("send partner update: %s", e)
            await query.answer("تعذر إرسال إشعار للطرف الآخر.", show_alert=True)
            return
        await query.answer("تم إرسال التحديثات للطرف الآخر ✅", show_alert=True)
    finally:
        db.close()


async def partner_batch_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if not data.startswith("ppart_ok_"):
        return
    batch_id = data.replace("ppart_ok_", "", 1)
    batch = (context.bot_data.get("partner_batches") or {}).get(batch_id)
    if not batch:
        await query.edit_message_text("انتهت صلاحية الطلب. اطلب إرسالاً جديداً.")
        return
    db = SessionLocal()
    try:
        link = db.query(PartnerLink).filter(PartnerLink.id == batch["link_id"]).first()
        if not link or link.status != "accepted":
            await query.edit_message_text("الربط غير صالح.")
            return
        user = db.query(User).filter(User.telegram_id == update.effective_user.id).first()
        if not user or user.id not in (link.inviter_user_id, link.invitee_user_id):
            await query.edit_message_text("غير مسموح.")
            return
        pids = batch.get("pending_ids") or []
        if not pids:
            await query.edit_message_text("لا توجد معاملات في هذه الدفعة.")
            return
        first_p = db.query(PartnerPendingTx).filter(PartnerPendingTx.id == pids[0]).first()
        if not first_p:
            await query.edit_message_text("انتهت صلاحية البيانات.")
            return
        src0 = db.query(CustomerTransaction).filter(CustomerTransaction.id == first_p.source_tx_id).first()
        if not src0:
            await query.edit_message_text("معاملة غير موجودة.")
            return
        tc0 = _mirror_customer_id(link, src0.customer_id)
        tcust0 = db.query(Customer).filter(Customer.id == tc0).first() if tc0 else None
        if not tcust0 or tcust0.user_id != user.id:
            await query.edit_message_text("لا يمكنك الموافقة على هذه الدفعة.")
            return
        approved = 0
        for pid in pids:
            p = db.query(PartnerPendingTx).filter(PartnerPendingTx.id == pid).first()
            if not p or p.status != "pending":
                continue
            src = db.query(CustomerTransaction).filter(CustomerTransaction.id == p.source_tx_id).first()
            if not src:
                continue
            target_cid = _mirror_customer_id(link, src.customer_id)
            if not target_cid:
                continue
            new_tx = CustomerTransaction(
                customer_id=target_cid,
                amount=src.amount,
                kind=invert_kind(src.kind),
                note=src.note,
                photo_file_id=src.photo_file_id,
            )
            db.add(new_tx)
            db.flush()
            p.status = "approved"
            p.mirrored_tx_id = new_tx.id
            approved += 1
        db.commit()
        await query.edit_message_text(
            f"✅ تمت الموافقة وأُضيفت {approved} معاملة لدفترك.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")]]
            ),
        )
        accepter_name = user.full_name or user.username or "المستخدم"
        sender_id = batch.get("sender_user_id")
        if sender_id and sender_id != user.id:
            sender_u = db.query(User).filter(User.id == sender_id).first()
            if sender_u and sender_u.telegram_id:
                try:
                    await context.bot.send_message(
                        chat_id=int(sender_u.telegram_id),
                        text=f"✅ {accepter_name} وافق على المعاملات وأُضيفت في دفتره.",
                        reply_markup=InlineKeyboardMarkup(
                            [[InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")]]
                        ),
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.exception("partner_batch_approve: %s", e)
        db.rollback()
        await query.edit_message_text("حدث خطأ أثناء المعالجة.")
    finally:
        db.close()
        context.bot_data.get("partner_batches", {}).pop(batch_id, None)


async def partner_batch_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if not data.startswith("ppart_no_"):
        return
    batch_id = data.replace("ppart_no_", "", 1)
    batch = (context.bot_data.get("partner_batches") or {}).pop(batch_id, None)
    if not batch:
        await query.edit_message_text("انتهت صلاحية الطلب.")
        return
    db = SessionLocal()
    try:
        link = db.query(PartnerLink).filter(PartnerLink.id == batch["link_id"]).first()
        user = db.query(User).filter(User.telegram_id == update.effective_user.id).first()
        for pid in batch.get("pending_ids", []):
            p = db.query(PartnerPendingTx).filter(PartnerPendingTx.id == pid).first()
            if p and p.status == "pending":
                p.status = "rejected"
        db.commit()
        await query.edit_message_text("❌ تم الرفض — لم تُضف المعاملات لدفترك.")
        # إشعار الطرف المرسل
        if link and user:
            rejecter = user.full_name or user.username or "المستخدم"
            sender_id = batch.get("sender_user_id")
            if sender_id and sender_id != user.id:
                sender_u = db.query(User).filter(User.id == sender_id).first()
                if sender_u and sender_u.telegram_id:
                    try:
                        await context.bot.send_message(
                            chat_id=int(sender_u.telegram_id),
                            text=f"❌ {rejecter} لم يقبل المعاملات المرسلة.",
                        )
                    except Exception:
                        pass
    finally:
        db.close()


async def handle_start_partner_link(
    update: Update, context: ContextTypes.DEFAULT_TYPE, token: str
) -> bool:
    """يُستدعى من /start — إن وُجدت دعوة صالحة يعرض قبول/رفض. يعيد True إذا عالج."""
    db = SessionLocal()
    try:
        link = db.query(PartnerLink).filter(PartnerLink.token == token).first()
        if not link or link.status != "pending":
            await update.message.reply_text("رابط الربط غير صالح أو منتهي.")
            return True
        inviter = db.query(User).filter(User.id == link.inviter_user_id).first()
        cust = db.query(Customer).filter(Customer.id == link.inviter_customer_id).first()
        if not inviter or not cust:
            await update.message.reply_text("بيانات الدعوة غير مكتملة.")
            return True
        tid = update.effective_user.id
        invitee = db.query(User).filter(User.telegram_id == tid).first()
        if not invitee:
            await update.message.reply_text(
                "يجب تسجيل الدخول أولاً في البوت، ثم اضغط الرابط مرة أخرى.\n\n"
                "استخدم /start ثم تسجيل الدخول."
            )
            return True
        if invitee.id == inviter.id:
            await update.message.reply_text("لا يمكنك قبول دعوتك لنفسك 🙂")
            return True
        inviter_name = inviter.full_name or inviter.username or "مستخدم"
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ قبول — إنشاء عميل وربط",
                        callback_data=f"plink_yes_{token}",
                    )
                ],
                [InlineKeyboardButton("❌ رفض", callback_data=f"plink_no_{token}")],
            ]
        )
        me = await context.bot.get_me()
        link_url = f"https://t.me/{me.username}?start=plink_{token}"
        wa_text = f"دعوة ربط دفتر من {inviter_name}:\n{link_url}"
        wa_url = f"https://api.whatsapp.com/send?text={quote(wa_text)}"
        kb.inline_keyboard.append([InlineKeyboardButton("📤 مشاركة", url=wa_url)])
        await update.message.reply_text(
            f"🔗 دعوة ربط دفتر\n\n"
            f"المستخدم: {inviter_name}\n"
            f"بعد القبول سيُنشأ عندك عميل باسم هذا المستخدم، وستصلك معاملاته للموافقة (معكوسة).\n"
            f"هل توافق؟",
            reply_markup=kb,
        )
        return True
    finally:
        db.close()


async def partner_link_accept_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    token = (query.data or "").replace("plink_yes_", "", 1)
    db = SessionLocal()
    try:
        link = db.query(PartnerLink).filter(PartnerLink.token == token).first()
        if not link or link.status != "pending":
            await query.edit_message_text("الدعوة لم تعد صالحة.")
            return
        invitee = db.query(User).filter(User.telegram_id == update.effective_user.id).first()
        if not invitee:
            await query.edit_message_text("سجّل الدخول أولاً.")
            return
        inviter = db.query(User).filter(User.id == link.inviter_user_id).first()
        src_cust = db.query(Customer).filter(Customer.id == link.inviter_customer_id).first()
        if not src_cust:
            await query.edit_message_text("العميل الأصلي غير موجود.")
            return
        # عند قبول الدعوة: الطرف الثاني يجب أن يرى “المُرسِل” كعميل، وليس اسم العميل الذي اختاره المُرسِل.
        # لذلك ننشئ Customer جديد باسم بيانات المُرسِل + رقم المُرسِل.
        mirrored_name = inviter.full_name or inviter.username or "مستخدم"
        mirrored_phone = inviter.phone or src_cust.phone
        new_c = Customer(
            user_id=invitee.id,
            name=mirrored_name,
            phone=mirrored_phone,
        )
        db.add(new_c)
        db.flush()
        link.invitee_user_id = invitee.id
        link.invitee_customer_id = new_c.id
        link.status = "accepted"
        db.commit()
        # صفّ معاملات الداعي القديمة كطابور موافقة عند الطرف الآخر
        for tx in (
            db.query(CustomerTransaction)
            .filter(CustomerTransaction.customer_id == link.inviter_customer_id)
            .order_by(CustomerTransaction.id.asc())
            .all()
        ):
            maybe_queue_partner_tx(db, tx)
        await query.edit_message_text(
            f"✅ تم الربط.\n\n"
            f"أُنشئ عميل «{new_c.name}» في دفترك.\n"
            "عندما يرسل الطرف الآخر «تحديثات» يمكنك الموافقة أو الرفض."
        )
        if inviter and inviter.telegram_id:
            try:
                nm = invitee.full_name or invitee.username or "مستخدم"
                await context.bot.send_message(
                    chat_id=int(inviter.telegram_id),
                    text=f"✅ {nm} قبل دعوة الربط.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")]]
                    ),
                )
            except Exception:
                pass
    except Exception as e:
        logger.exception("partner_link_accept: %s", e)
        db.rollback()
        await query.edit_message_text("تعذر إتمام الربط.")
    finally:
        db.close()


async def partner_link_reject_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    token = (query.data or "").replace("plink_no_", "", 1)
    db = SessionLocal()
    try:
        link = db.query(PartnerLink).filter(PartnerLink.token == token).first()
        if link and link.status == "pending":
            link.status = "cancelled"
            db.commit()
        await query.edit_message_text("تم رفض الدعوة.")
    finally:
        db.close()
