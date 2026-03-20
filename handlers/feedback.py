# -*- coding: utf-8 -*-
"""استقبال مشاكل/اقتراحات المستخدمين وإرسالها للإدارة."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from database import SessionLocal
from app_models import User, FeedbackMessage
from config import ADMIN_ID

FEEDBACK_WAIT = 1200


def _get_current_user(db, telegram_id: int):
    return db.query(User).filter(User.telegram_id == telegram_id).first()


async def _start_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE, source: str):
    query = update.callback_query
    await query.answer()
    context.user_data["feedback_source"] = source
    keyboard = [[InlineKeyboardButton("◀ رجوع", callback_data="feedback_back")]]
    await query.edit_message_text(
        "أرسل الآن رسالتك / ملاحظتك / المشكلة.\n"
        "يمكنك إرسال نص، صورة، فيديو، بصمة صوت، ملف أو أي وسائط.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return FEEDBACK_WAIT


async def feedback_from_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _start_feedback(update, context, "profile")


async def feedback_from_broadcast_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _start_feedback(update, context, "broadcast_comment")


async def feedback_from_broadcast_suggest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _start_feedback(update, context, "broadcast_suggest")


async def feedback_back_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("feedback_source", None)
    await query.edit_message_text(
        "تم الرجوع ✅",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")]]
        ),
    )
    return ConversationHandler.END


def _extract_message_payload(msg):
    if msg.text:
        return "text", msg.text, None
    if msg.photo:
        return "photo", msg.caption or "", msg.photo[-1].file_id
    if msg.video:
        return "video", msg.caption or "", msg.video.file_id
    if msg.voice:
        return "voice", msg.caption or "", msg.voice.file_id
    if msg.document:
        return "document", msg.caption or "", msg.document.file_id
    if msg.audio:
        return "audio", msg.caption or "", msg.audio.file_id
    if msg.sticker:
        return "sticker", "", msg.sticker.file_id
    return "unknown", "", None


async def feedback_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return FEEDBACK_WAIT
    db = SessionLocal()
    try:
        user = _get_current_user(db, update.effective_user.id)
        source = context.user_data.get("feedback_source", "profile")
        content_type, text, file_id = _extract_message_payload(msg)

        rec = FeedbackMessage(
            user_id=(user.id if user else None),
            user_telegram_id=update.effective_user.id,
            user_name=(user.full_name if user else (update.effective_user.full_name or "")),
            user_phone=(user.phone if user else None),
            source=source,
            content_type=content_type,
            text=text or None,
            file_id=file_id,
        )
        db.add(rec)
        db.commit()

        # Forward to admin
        if ADMIN_ID:
            header = (
                "📥 رسالة جديدة (مشاكل/اقتراحات)\n\n"
                f"المرسل: {rec.user_name or '—'}\n"
                f"الهاتف: {rec.user_phone or '—'}\n"
                f"Telegram ID: {rec.user_telegram_id}\n"
                f"النوع: {source}\n"
                f"المحتوى: {content_type}\n"
            )
            if text:
                header += f"\nالنص:\n{text}"

            if content_type == "text":
                await context.bot.send_message(ADMIN_ID, header)
            elif file_id:
                if content_type == "photo":
                    await context.bot.send_photo(ADMIN_ID, file_id, caption=header[:1024])
                elif content_type == "video":
                    await context.bot.send_video(ADMIN_ID, file_id, caption=header[:1024])
                elif content_type == "voice":
                    await context.bot.send_voice(ADMIN_ID, file_id, caption=header[:1024])
                elif content_type == "document":
                    await context.bot.send_document(ADMIN_ID, file_id, caption=header[:1024])
                elif content_type == "audio":
                    await context.bot.send_audio(ADMIN_ID, file_id, caption=header[:1024])
                elif content_type == "sticker":
                    await context.bot.send_sticker(ADMIN_ID, file_id)
                    await context.bot.send_message(ADMIN_ID, header)
                else:
                    await context.bot.send_message(ADMIN_ID, header)
            else:
                await context.bot.send_message(ADMIN_ID, header)

        await msg.reply_text(
            "تم استلام رسالتك ✅\nشكراً لك.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀ حسابي", callback_data="menu_profile")]]
            ),
        )
    finally:
        db.close()
    context.user_data.pop("feedback_source", None)
    return ConversationHandler.END

