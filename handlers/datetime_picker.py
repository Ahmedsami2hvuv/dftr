# -*- coding: utf-8 -*-
"""لوحة أزرار مشتركة: سنة ← شهر ← يوم ← ساعة ← دقيقة (تعديل معاملة + تذكير تسديد)."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

MODE_TX = "tx"
MODE_RM = "rm"

MONTHS_AR = (
    "يناير",
    "فبراير",
    "مارس",
    "أبريل",
    "مايو",
    "يونيو",
    "يوليو",
    "أغسطس",
    "سبتمبر",
    "أكتوبر",
    "نوفمبر",
    "ديسمبر",
)

# 24 ساعة حسب تخطيط المستخدم: صفوف من 3
HOUR_ROWS = (
    ((1, "1ف"), (2, "2ف"), (3, "3ف")),
    ((4, "4ف"), (5, "5ف"), (6, "6ص")),
    ((7, "7ص"), (8, "8ص"), (9, "9ص")),
    ((10, "10ص"), (11, "11ص"), (12, "12ض")),
    ((13, "1ض"), (14, "2ض"), (15, "3ض")),
    ((16, "4ع"), (17, "5ع"), (18, "6م")),
    ((19, "7م"), (20, "8ل"), (21, "9ل")),
    ((22, "10ل"), (23, "11ل"), (0, "12ل")),
)

# دقائق (مع 45 في الصف الثالث)
MINUTE_ROWS = (
    (0, 5, 10),
    (15, 20, 25),
    (30, 35, 40, 45),
    (50, 55, 59),
)


def _dt_prefix(mode: str, eid: int) -> str:
    return f"dt_{mode}_{eid}"


def clear_dt_user_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in ("dt_mode", "dt_eid", "dt_y", "dt_m", "dt_d", "dt_h"):
        context.user_data.pop(k, None)


def _year_range() -> list[int]:
    y = date.today().year
    return list(range(y, y + 11))


def kb_years(mode: str, eid: int) -> InlineKeyboardMarkup:
    pref = _dt_prefix(mode, eid)
    rows = []
    years = _year_range()
    for i in range(0, len(years), 3):
        chunk = years[i : i + 3]
        rows.append(
            [InlineKeyboardButton(str(y), callback_data=f"{pref}_y_{y}") for y in chunk]
        )
    rows.append([InlineKeyboardButton("◀ رجوع", callback_data=f"{pref}_b")])
    return InlineKeyboardMarkup(rows)


def kb_months(mode: str, eid: int, year: int) -> InlineKeyboardMarkup:
    pref = _dt_prefix(mode, eid)
    rows = []
    for i in range(0, 12, 3):
        row = []
        for m in range(i + 1, i + 4):
            if m <= 12:
                row.append(
                    InlineKeyboardButton(
                        MONTHS_AR[m - 1], callback_data=f"{pref}_m_{m}"
                    )
                )
        rows.append(row)
    rows.append([InlineKeyboardButton("◀ رجوع", callback_data=f"{pref}_b")])
    return InlineKeyboardMarkup(rows)


def kb_days(mode: str, eid: int, year: int, month: int) -> InlineKeyboardMarkup:
    pref = _dt_prefix(mode, eid)
    _, n_days = calendar.monthrange(year, month)
    rows = []
    d = 1
    while d <= n_days:
        row = []
        for _ in range(4):
            if d <= n_days:
                row.append(
                    InlineKeyboardButton(str(d), callback_data=f"{pref}_d_{d}")
                )
                d += 1
        rows.append(row)
    rows.append([InlineKeyboardButton("◀ رجوع", callback_data=f"{pref}_b")])
    return InlineKeyboardMarkup(rows)


def kb_hours(mode: str, eid: int) -> InlineKeyboardMarkup:
    pref = _dt_prefix(mode, eid)
    rows = []
    for row in HOUR_ROWS:
        buttons = []
        for h24, label in row:
            buttons.append(
                InlineKeyboardButton(label, callback_data=f"{pref}_h_{h24}")
            )
        rows.append(buttons)
    rows.append([InlineKeyboardButton("◀ رجوع", callback_data=f"{pref}_b")])
    return InlineKeyboardMarkup(rows)


def kb_minutes(mode: str, eid: int) -> InlineKeyboardMarkup:
    pref = _dt_prefix(mode, eid)
    rows = []
    for tup in MINUTE_ROWS:
        row = []
        for mn in tup:
            row.append(
                InlineKeyboardButton(f"{mn:02d}د", callback_data=f"{pref}_n_{mn:02d}")
            )
        rows.append(row)
    rows.append([InlineKeyboardButton("◀ رجوع", callback_data=f"{pref}_b")])
    return InlineKeyboardMarkup(rows)


async def start_tx_datetime_pick(
    update: Update, context: ContextTypes.DEFAULT_TYPE, tx_id: int
) -> int:
    """يُفترض أن callback_query مُجابٌ من المتصل."""
    clear_dt_user_data(context)
    context.user_data["dt_mode"] = MODE_TX
    context.user_data["dt_eid"] = tx_id
    query = update.callback_query
    y0 = date.today().year
    y1 = y0 + 10
    await query.edit_message_text(
        "📅 تعديل التاريخ والوقت\n\n"
        f"اختر السنة ({y0} — {y1}):\n"
        "ثم الشهر → اليوم → الساعة → الدقيقة.",
        reply_markup=kb_years(MODE_TX, tx_id),
    )
    from handlers.customers import TX_EDIT_DATE

    return TX_EDIT_DATE


async def start_reminder_datetime_pick(
    update: Update, context: ContextTypes.DEFAULT_TYPE, cid: int
) -> int:
    """يُفترض أن callback_query مُجابٌ من المتصل."""
    clear_dt_user_data(context)
    context.user_data["dt_mode"] = MODE_RM
    context.user_data["dt_eid"] = cid
    query = update.callback_query
    y0 = date.today().year
    y1 = y0 + 10
    await query.edit_message_text(
        "🔔 تذكير التسديد — وقت الاستحقاق\n\n"
        f"اختر السنة ({y0} — {y1}):\n"
        "ثم الشهر → اليوم → الساعة → الدقيقة.",
        reply_markup=kb_years(MODE_RM, cid),
    )
    from handlers.reminder import REMIND_DUE_DATE

    return REMIND_DUE_DATE


async def handle_datetime_picker(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Optional[int]:
    """
    يعيد حالة المحادثة التالية أو END أو None إن لم يُعالَج.
    """
    from handlers.customers import apply_tx_datetime_from_picker
    from handlers.reminder import show_reminder_offset_after_datetime

    query = update.callback_query
    if not query or not query.data:
        return None
    data = query.data
    m = re.match(r"^dt_(tx|rm)_(\d+)_b$", data)
    if m:
        mode, eid_s = m.group(1), m.group(2)
        eid = int(eid_s)
        await query.answer()
        if context.user_data.get("dt_eid") != eid or context.user_data.get("dt_mode") != mode:
            await query.edit_message_text("انتهت الجلسة.")
            clear_dt_user_data(context)
            return ConversationHandler.END
        # رجوع خطوة
        if "dt_h" in context.user_data:
            context.user_data.pop("dt_h", None)
            y, mo, d = (
                context.user_data["dt_y"],
                context.user_data["dt_m"],
                context.user_data["dt_d"],
            )
            await query.edit_message_text(
                f"📅 الساعة — اليوم: {d}/{mo}/{y}",
                reply_markup=kb_hours(mode, eid),
            )
            return None
        if "dt_d" in context.user_data:
            context.user_data.pop("dt_d", None)
            y, mo = context.user_data["dt_y"], context.user_data["dt_m"]
            await query.edit_message_text(
                f"📅 اليوم — {MONTHS_AR[mo - 1]} {y}",
                reply_markup=kb_days(mode, eid, y, mo),
            )
            return None
        if "dt_m" in context.user_data:
            context.user_data.pop("dt_m", None)
            y = context.user_data["dt_y"]
            await query.edit_message_text(
                f"📅 الشهر — السنة {y}",
                reply_markup=kb_months(mode, eid, y),
            )
            return None
        if "dt_y" in context.user_data:
            context.user_data.pop("dt_y", None)
            y0 = date.today().year
            y1 = y0 + 10
            title = (
                "📅 تعديل التاريخ والوقت"
                if mode == MODE_TX
                else "🔔 تذكير التسديد — وقت الاستحقاق"
            )
            await query.edit_message_text(
                f"{title}\n\nاختر السنة ({y0} — {y1}):",
                reply_markup=kb_years(mode, eid),
            )
            return None
        # من شاشة السنة: خروج كامل
        clear_dt_user_data(context)
        if mode == MODE_TX:
            context.user_data.pop("tx_edit_id", None)
            await query.edit_message_text("تم الرجوع.")
            return ConversationHandler.END
        context.user_data.pop("reminder_cid", None)
        await query.edit_message_text("تم الرجوع.")
        return ConversationHandler.END

    m = re.match(r"^dt_(tx|rm)_(\d+)_y_(\d{4})$", data)
    if m:
        mode, eid_s, y = m.group(1), m.group(2), int(m.group(3))
        eid = int(eid_s)
        if mode == MODE_TX and context.user_data.get("tx_edit_id") != eid:
            await query.answer("جلسة غير صالحة.", show_alert=True)
            return ConversationHandler.END
        if mode == MODE_RM and context.user_data.get("reminder_cid") != eid:
            await query.answer("جلسة غير صالحة.", show_alert=True)
            return ConversationHandler.END
        await query.answer()
        context.user_data["dt_mode"] = mode
        context.user_data["dt_eid"] = eid
        context.user_data["dt_y"] = y
        context.user_data.pop("dt_m", None)
        context.user_data.pop("dt_d", None)
        context.user_data.pop("dt_h", None)
        await query.edit_message_text(
            f"📅 الشهر — السنة {y}",
            reply_markup=kb_months(mode, eid, y),
        )
        return None

    m = re.match(r"^dt_(tx|rm)_(\d+)_m_(\d{1,2})$", data)
    if m:
        mode, eid_s, mo = m.group(1), m.group(2), int(m.group(3))
        eid = int(eid_s)
        await query.answer()
        y = context.user_data.get("dt_y")
        if y is None or mo < 1 or mo > 12:
            await query.edit_message_text("خطأ في البيانات.")
            return ConversationHandler.END
        context.user_data["dt_m"] = mo
        context.user_data.pop("dt_d", None)
        context.user_data.pop("dt_h", None)
        await query.edit_message_text(
            f"📅 اليوم — {MONTHS_AR[mo - 1]} {y}",
            reply_markup=kb_days(mode, eid, y, mo),
        )
        return None

    m = re.match(r"^dt_(tx|rm)_(\d+)_d_(\d{1,2})$", data)
    if m:
        mode, eid_s, d = m.group(1), m.group(2), int(m.group(3))
        eid = int(eid_s)
        y = context.user_data.get("dt_y")
        mo = context.user_data.get("dt_m")
        if y is None or mo is None:
            await query.answer()
            await query.edit_message_text("انتهت الجلسة.")
            return ConversationHandler.END
        _, nmax = calendar.monthrange(y, mo)
        if d < 1 or d > nmax:
            await query.answer("يوم غير صالح.", show_alert=True)
            return None
        await query.answer()
        context.user_data["dt_d"] = d
        context.user_data.pop("dt_h", None)
        await query.edit_message_text(
            f"📅 الساعة — {d}/{mo}/{y}",
            reply_markup=kb_hours(mode, eid),
        )
        return None

    m = re.match(r"^dt_(tx|rm)_(\d+)_h_(\d{1,2})$", data)
    if m:
        mode, eid_s, h = m.group(1), m.group(2), int(m.group(3))
        eid = int(eid_s)
        if h < 0 or h > 23:
            await query.answer("ساعة غير صالحة.", show_alert=True)
            return None
        await query.answer()
        y = context.user_data.get("dt_y")
        mo = context.user_data.get("dt_m")
        d = context.user_data.get("dt_d")
        if y is None or mo is None or d is None:
            await query.edit_message_text("انتهت الجلسة.")
            return ConversationHandler.END
        context.user_data["dt_h"] = h
        await query.edit_message_text(
            f"📅 الدقيقة — {d}/{mo}/{y} — الساعة {h:02d}",
            reply_markup=kb_minutes(mode, eid),
        )
        return None

    m = re.match(r"^dt_(tx|rm)_(\d+)_n_(\d{2})$", data)
    if m:
        mode, eid_s, mn_s = m.group(1), m.group(2), m.group(3)
        eid = int(eid_s)
        mn = int(mn_s)
        if mn not in range(60):
            await query.answer("دقيقة غير صالحة.", show_alert=True)
            return None
        y = context.user_data.get("dt_y")
        mo = context.user_data.get("dt_m")
        d = context.user_data.get("dt_d")
        h = context.user_data.get("dt_h")
        if y is None or mo is None or d is None or h is None:
            await query.answer()
            await query.edit_message_text("انتهت الجلسة.")
            return ConversationHandler.END
        try:
            dt = datetime(y, mo, d, h, mn, 0)
        except ValueError:
            await query.answer("تاريخ غير صالح.", show_alert=True)
            return None
        await query.answer()
        clear_dt_user_data(context)
        if mode == MODE_TX:
            return await apply_tx_datetime_from_picker(update, context, eid, dt)
        return await show_reminder_offset_after_datetime(
            update, context, eid, dt
        )

    return None
