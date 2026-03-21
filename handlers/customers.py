# -*- coding: utf-8 -*-
"""دفتر الديون: عملاء، أخذت/أعطيت، مشاركة"""
import re
import secrets
from io import BytesIO
from urllib.parse import quote
from urllib.parse import urlparse
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime, timedelta

from telegram import InputFile, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import func
from database import SessionLocal
from app_models import User, Customer, CustomerTransaction, ShareLink, CustomerCategory
from app_models.partner import PartnerLink, PartnerPendingTx, CustomerPaymentReminder
from utils.phone import is_plausible_iraq_mobile, normalize_phone, wa_number
from config import WEB_BASE_URL, WEB_TX_UPLOAD_DIR, public_web_base_url_for_telegram_fetch
from handlers.inline_nav import kb_main_menu, kb_menu_customers, kb_tx_detail

(
    CUST_NAME,
    CUST_PHONE,
    CUST_SEARCH_QUERY,
    CUST_AMOUNT,
    CUST_NOTE,
    CUST_EDIT_NAME,
    CUST_EDIT_PHONE,
) = range(7)

TX_PAGE_SIZE = 15

(TX_EDIT_AMOUNT, TX_EDIT_NOTE, TX_EDIT_DATE, TX_EDIT_PHOTO) = range(4)

(CAT_ADD_NAME, CAT_ADD_KIND) = range(200, 202)


def get_current_user(db, telegram_id: int):
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def _calc_amount_tokens(expr: str) -> list[str]:
    """تحليل تعبير حسابي بسيط للأرقام + و- و* و/ فقط (بدون فواصِل/أقواس)."""
    expr = (expr or "").strip()
    if not expr:
        return []

    tokens: list[str] = []
    num = ""
    allowed = set("0123456789.")
    ops = set("+-*/")

    for ch in expr:
        if ch in allowed:
            num += ch
            continue
        if ch in ops:
            if not num:
                raise ValueError("تعبير غير صالح")
            tokens.append(num)
            tokens.append(ch)
            num = ""
            continue
        raise ValueError("حرف غير مسموح")

    if num:
        tokens.append(num)
    return tokens


def _calc_amount_compute(expr: str) -> Decimal:
    """حساب تعبير بسيط باستخدام Decimal فقط."""
    expr = (expr or "").strip().replace(" ", "")
    if not expr:
        raise ValueError("تعبير غير صالح")

    # تحقق سريع من الصيغة (أرقام + فاصل عشري) مع عمليات فقط.
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)?([+\-*/][0-9]+(\.[0-9]+)?)*", expr):
        raise ValueError("تعبير غير صالح")

    tokens = _calc_amount_tokens(expr)
    precedence = {"+": 1, "-": 1, "*": 2, "/": 2}

    # تحويل RPN (Shunting-yard) ثم حساب.
    out: list[str] = []
    stack: list[str] = []
    for t in tokens:
        if t in precedence:
            while stack and stack[-1] in precedence and precedence[stack[-1]] >= precedence[t]:
                out.append(stack.pop())
            stack.append(t)
        else:
            out.append(t)
    while stack:
        out.append(stack.pop())

    eval_stack: list[Decimal] = []
    for t in out:
        if t in precedence:
            if len(eval_stack) < 2:
                raise ValueError("تعبير غير صالح")
            b = eval_stack.pop()
            a = eval_stack.pop()
            if t == "+":
                eval_stack.append(a + b)
            elif t == "-":
                eval_stack.append(a - b)
            elif t == "*":
                eval_stack.append(a * b)
            elif t == "/":
                if b == 0:
                    raise ZeroDivisionError("القسمة على صفر")
                eval_stack.append(a / b)
        else:
            eval_stack.append(Decimal(t))

    if len(eval_stack) != 1:
        raise ValueError("تعبير غير صالح")

    res = eval_stack[0]
    return res.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _calc_amount_display(expr: str) -> str:
    """تنسيق قيمة رقمية ليظهر بشكل ثابت بسنتين."""
    if expr is None:
        return "0.00"
    expr = str(expr)
    if "." in expr:
        # لو كانت 3+ منازل، نقرب لعرض أفضل.
        try:
            return _calc_amount_compute(expr).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).__format__(
                ".2f"
            )
        except Exception:
            pass
    try:
        d = Decimal(expr)
        return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).__format__(".2f")
    except Exception:
        return expr


def _kb_cust_amount_calc(cid: int | None, expr_display: str):
    """لوحة حاسبة مبلغ المعاملة."""
    row_back = []
    if cid:
        row_back.append(InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_txn_back_{cid}"))
    row_back.append(InlineKeyboardButton("◀ رجوع لقائمة العملاء", callback_data="cust_txn_exit"))

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🧮 إخفاء الحاسبة", callback_data="calc_amt_hide"),
                InlineKeyboardButton("🧹 مسح", callback_data="calc_amt_clear"),
            ],
            [
                InlineKeyboardButton("⌫", callback_data="calc_amt_backspace"),
                InlineKeyboardButton("÷", callback_data="calc_amt_op_div"),
            ],
            [
                InlineKeyboardButton("7", callback_data="calc_amt_digit_7"),
                InlineKeyboardButton("8", callback_data="calc_amt_digit_8"),
                InlineKeyboardButton("9", callback_data="calc_amt_digit_9"),
                InlineKeyboardButton("×", callback_data="calc_amt_op_mul"),
            ],
            [
                InlineKeyboardButton("4", callback_data="calc_amt_digit_4"),
                InlineKeyboardButton("5", callback_data="calc_amt_digit_5"),
                InlineKeyboardButton("6", callback_data="calc_amt_digit_6"),
                InlineKeyboardButton("-", callback_data="calc_amt_op_sub"),
            ],
            [
                InlineKeyboardButton("1", callback_data="calc_amt_digit_1"),
                InlineKeyboardButton("2", callback_data="calc_amt_digit_2"),
                InlineKeyboardButton("3", callback_data="calc_amt_digit_3"),
                InlineKeyboardButton("+", callback_data="calc_amt_op_add"),
            ],
            [
                InlineKeyboardButton("0", callback_data="calc_amt_digit_0"),
                InlineKeyboardButton(".", callback_data="calc_amt_digit_dot"),
                InlineKeyboardButton("=", callback_data="calc_amt_equals"),
            ],
            [InlineKeyboardButton(f"💰 {expr_display}", callback_data="calc_amt_noop")],
            [InlineKeyboardButton("✅ إدخال المبلغ", callback_data="calc_amt_submit")],
            row_back,
        ]
    )


async def cust_calc_amount_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حاسبة تفاعلية لإدخال مبلغ المعاملة داخل حالة CUST_AMOUNT."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""

    # cid مستخدم في أزرار الرجوع فقط.
    cid = context.user_data.get("cust_txn_cid")
    cid = int(cid) if cid else None

    # تحقق ملكية العميل حتى لا تعمل أزرار الحاسبة القديمة من جلسات/رسائل سابقة.
    # هذا يمنع أن تضغط زر مثل "✅ إدخال المبلغ" وما زال البوت داخل تدفق خاطئ.
    if cid:
        db = SessionLocal()
        try:
            user = get_current_user(db, update.effective_user.id)
            cust = db.query(Customer).filter(Customer.id == cid).first()
            if not user or not cust or cust.user_id != user.id:
                await query.edit_message_text(
                    "يجب تسجيل الدخول أولاً (أو هذه العملية غير مسموحة).",
                    reply_markup=kb_main_menu(),
                )
                context.user_data.pop("cust_calc_expr", None)
                context.user_data.pop("cust_calc_last_was_equals", None)
                context.user_data.pop("cust_txn_amount", None)
                context.user_data.pop("cust_txn_kind", None)
                context.user_data.pop("cust_txn_cid", None)
                return ConversationHandler.END
        finally:
            db.close()

    if data == "calc_amt_hide":
        kind = context.user_data.get("cust_txn_kind")
        kind_label = "أخذت 🔴" if kind == "took" else "أعطيت 🟢"
        back_row = (
            [InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_txn_back_{cid}"), InlineKeyboardButton("◀ رجوع لقائمة العملاء", callback_data="cust_txn_exit")]
            if cid
            else None
        )
        kb = [([InlineKeyboardButton("🧮 إظهار الحاسبة", callback_data="calc_amt_open")])]
        if back_row:
            kb.append(back_row)
        await query.edit_message_text(
            f"({kind_label})\n\n"
            "ارسل المبلغ\n"
            "وبعدها انقر على ✅ إدخال المبلغ",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return CUST_AMOUNT

    if data == "calc_amt_open" or data == "calc_amt_clear" or "calc_amt_clear" == data:
        context.user_data["cust_calc_expr"] = "0"
        context.user_data["cust_calc_last_was_equals"] = False
    elif data == "calc_amt_backspace":
        expr = context.user_data.get("cust_calc_expr") or "0"
        if context.user_data.get("cust_calc_last_was_equals"):
            context.user_data["cust_calc_last_was_equals"] = False
            # يسمح بالحذف من الناتج
        if expr in ("0", "0.00", ""):
            expr = "0"
        else:
            expr = expr[:-1]
            if not expr or expr == "-":
                expr = "0"
        context.user_data["cust_calc_expr"] = expr
    elif data.startswith("calc_amt_digit_") or data == "calc_amt_digit_dot":
        expr = context.user_data.get("cust_calc_expr") or "0"
        if context.user_data.get("cust_calc_last_was_equals"):
            # بعد "=": الرقم الجديد يبدأ تعبير جديد.
            expr = "0"
            context.user_data["cust_calc_last_was_equals"] = False

        if data == "calc_amt_digit_dot":
            digit = "."
        else:
            digit = data.replace("calc_amt_digit_", "", 1)

        if digit == ".":
            if expr.endswith(("+", "-", "*", "/")):
                expr = expr + "0."
            else:
                # تحقق إذا الرقم الأخير يحتوي '.'.
                last_op_pos = max(expr.rfind("+"), expr.rfind("-"), expr.rfind("*"), expr.rfind("/"))
                last_num = expr[last_op_pos + 1 :]
                if "." in last_num:
                    # تجاهل '.' إذا موجود.
                    context.user_data["cust_calc_expr"] = expr
                    return CUST_AMOUNT
                if expr == "0":
                    expr = "0."
                else:
                    expr = expr + "."
        else:
            if expr in ("0", "0.00") and len(expr) <= 2:
                # صفر بادئ.
                expr = digit
            elif expr.endswith(("+", "-", "*", "/")):
                expr = expr + digit
            else:
                expr = expr + digit

        context.user_data["cust_calc_expr"] = expr
    elif data.startswith("calc_amt_op_"):
        expr = context.user_data.get("cust_calc_expr") or "0"
        op_map = {
            "calc_amt_op_add": "+",
            "calc_amt_op_sub": "-",
            "calc_amt_op_mul": "*",
            "calc_amt_op_div": "/",
        }
        op = op_map.get(data)
        if not op:
            return CUST_AMOUNT

        context.user_data["cust_calc_last_was_equals"] = False
        if expr.endswith(("+", "-", "*", "/")):
            expr = expr[:-1] + op
        else:
            expr = expr + op
        context.user_data["cust_calc_expr"] = expr
    elif data == "calc_amt_equals":
        expr = context.user_data.get("cust_calc_expr") or "0"
        try:
            result = _calc_amount_compute(expr)
        except Exception:
            await query.edit_message_text(
                "تعبير غير صالح. جرّب من جديد.",
                reply_markup=_kb_cust_amount_calc(cid, context.user_data.get("cust_calc_expr") or "0"),
            )
            return CUST_AMOUNT
        context.user_data["cust_calc_expr"] = f"{result:.2f}"
        context.user_data["cust_calc_last_was_equals"] = True
    elif data == "calc_amt_submit":
        expr = context.user_data.get("cust_calc_expr") or "0"
        try:
            amount = _calc_amount_compute(expr)
        except ZeroDivisionError:
            await query.edit_message_text(
                "لا يمكن القسمة على صفر.",
                reply_markup=_kb_cust_amount_calc(cid, context.user_data.get("cust_calc_expr") or "0"),
            )
            return CUST_AMOUNT
        except Exception:
            await query.edit_message_text(
                "تعبير غير صالح. صححه ثم اضغط إدخال المبلغ.",
                reply_markup=_kb_cust_amount_calc(cid, context.user_data.get("cust_calc_expr") or "0"),
            )
            return CUST_AMOUNT

        context.user_data.pop("cust_calc_expr", None)
        context.user_data.pop("cust_calc_last_was_equals", None)
        context.user_data["cust_txn_amount"] = amount
        kind = context.user_data.get("cust_txn_kind")

        # أزل أزرار الحاسبة من الرسالة السابقة.
        try:
            await query.edit_message_text(
                f"تم استلام المبلغ ✅\nالمبلغ: {amount:.2f} د.ع.",
            )
        except Exception:
            pass

        keyboard = [
            [
                InlineKeyboardButton(
                    "⏭️ تخطي الملاحظة",
                    callback_data="cust_note_skip_btn",
                )
            ],
            [
                InlineKeyboardButton("↩ رجوع لتعديل السعر", callback_data="cust_txn_back_amount"),
                InlineKeyboardButton("◀ رجوع لقائمة العملاء", callback_data="cust_txn_exit"),
            ],
        ]

        await query.message.reply_text(
            "تم استلام المبلغ ✅\n\nأرسل ملاحظة أو صورة.\n"
            "يمكنك استخدام «تخطي الملاحظة» إن لم تكن هناك ملاحظة.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return CUST_NOTE
    elif data == "calc_amt_noop":
        pass
    else:
        # زر غير معروف.
        return CUST_AMOUNT

    expr = context.user_data.get("cust_calc_expr") or "0"
    expr_display = expr
    # لو كان Expr رقم كامل: نظهره بشكل منسق أكثر.
    try:
        expr_display = _calc_amount_compute(expr) if re.fullmatch(r"[0-9]+(\.[0-9]+)?", expr) else expr
        if isinstance(expr_display, Decimal):
            expr_display = f"{expr_display:.2f}"
    except Exception:
        expr_display = expr

    await query.edit_message_text(
        "🧮 حاسبة مبلغ المعاملة\n\nاكتب العملية ثم اضغط `=` ثم `✅ إدخال المبلغ`.",
        reply_markup=_kb_cust_amount_calc(cid, expr_display),
    )
    return CUST_AMOUNT


def _kb_cust_txn_flow(cid: int | None) -> InlineKeyboardMarkup:
    """أزرار رجوع أثناء إدخال معاملة (مبلغ/ملاحظة)."""
    if cid:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_txn_back_{cid}")],
                [InlineKeyboardButton("◀ رجوع لقائمة العملاء", callback_data="cust_txn_exit")],
            ]
        )
    return kb_menu_customers()


def _kb_cust_cat_back(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    back_cid = context.user_data.get("cust_cat_back_customer_id")
    if back_cid:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ رجوع", callback_data=f"cust_categories_menu_{back_cid}")]]
        )
    return kb_menu_customers()


def _balance(customer):
    gave = sum(t.amount for t in customer.transactions if t.kind == "gave")
    took = sum(t.amount for t in customer.transactions if t.kind == "took")
    return float(gave - took), float(gave), float(took)


def _balance_status_emoji(bal: float) -> str:
    """يطابق لون «الرصيد الحالي» في صفحة العميل: موجب 🟢، سالب 🔴، صفر ⚪."""
    if bal > 0:
        return "🟢"
    if bal < 0:
        return "🔴"
    return "⚪"


def _normalize_amount_digits(s: str) -> str:
    """تحويل الأرقام العربية/الفارسية إلى إنجليزية لتحليل المبلغ."""
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    s = s.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    return s


def _parse_decimal_amount_token(s: str):
    """تحليل رقم واحد فقط (سطر كامل = المبلغ فقط)."""
    s = _normalize_amount_digits(s.strip())
    s = s.replace(",", "").replace("،", "").replace(" ", "")
    if not s:
        return None
    try:
        d = Decimal(s)
        return d if d > 0 else None
    except Exception:
        return None


def _is_pure_amount_line(s: str) -> bool:
    """السطر يحتوي رقماً فقط (بدون نص ملاحظة)."""
    s = _normalize_amount_digits(s.strip())
    s = s.replace(",", "").replace("،", "").replace(" ", "")
    if not s or not re.match(r"^[\d.]+$", s):
        return False
    try:
        return Decimal(s) > 0
    except Exception:
        return False


def _parse_name_and_phone_from_text(text: str) -> tuple[str, str | None]:
    """
    يستخرج (اسم، رقم خام) من رسالة واحدة أو سطرين:
    - سطران: اسم ثم رقم / رقم ثم اسم / عدة أسطر اسم ثم سطر رقم
    - سطر واحد: كلمات ثم رقم في النهاية (أو رقم ثم اسم)
    إذا لم يُعثر على رقم صالح: (النص كاملاً كاسم، None).
    إذا كان النص رقماً فقط بلا اسم: ("", None).
    """
    text = (text or "").strip()
    if not text:
        return "", None

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 2:
        last = lines[-1]
        if is_plausible_iraq_mobile(normalize_phone(last)):
            name = " ".join(lines[:-1]).strip()
            if name:
                return name, last.strip()
        first = lines[0]
        if is_plausible_iraq_mobile(normalize_phone(first)):
            name = " ".join(lines[1:]).strip()
            if name:
                return name, first.strip()
        return " ".join(lines).strip(), None

    parts = lines[0].split()
    if len(parts) == 1:
        only = parts[0]
        if is_plausible_iraq_mobile(normalize_phone(only)):
            return "", None
        return only, None

    for i in range(len(parts) - 1, -1, -1):
        chunk = " ".join(parts[i:])
        if is_plausible_iraq_mobile(normalize_phone(chunk)):
            name = " ".join(parts[:i]).strip()
            if name:
                return name, chunk
    return text, None


async def _save_new_customer_from_add_flow(
    update: Update, context: ContextTypes.DEFAULT_TYPE, name: str, phone_norm: str | None
) -> bool:
    """يحفظ عميلاً جديداً ويرد برسالة نجاح. يعيد False إن لم يُحفظ."""
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await update.message.reply_text("انتهت الجلسة. استخدم /start", reply_markup=kb_main_menu())
            return False
        c = Customer(user_id=user.id, name=name, phone=phone_norm)
        db.add(c)
        db.commit()
        db.refresh(c)
        keyboard = [
            [InlineKeyboardButton("عرض العميل", callback_data=f"cust_{c.id}")],
            [InlineKeyboardButton("◀ قائمة العملاء", callback_data="menu_customers")],
        ]
        await update.message.reply_text(
            f"تمت إضافة العميل ✅ {c.name}" + (f" — {c.phone}" if c.phone else ""),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return True
    finally:
        db.close()


def _looks_like_phone_not_amount(s: str) -> bool:
    """تمييز أرقام هواتف عراقية شائعة عن المبالغ."""
    t = _normalize_amount_digits(s.strip()).replace(" ", "")
    if t.startswith("964"):
        rest = t[3:].lstrip("0")
        t = ("0" + rest) if rest else t
    if re.match(r"^07\d{9}$", t):
        return True
    if re.match(r"^7\d{9}$", t) and len(t) == 10:
        return True
    return False


def _parse_single_line_amount_note(line: str):
    """
    أمثلة: 38 | 38 الفيروز | 38,500 باقي
    يعيد (Decimal, ملاحظة أو None)
    """
    line = (line or "").strip()
    if not line:
        return None, None
    line = _normalize_amount_digits(line)
    m = re.match(r"^([\d,\.]+)\s*(.*)$", line.strip())
    if not m:
        return None, None
    num_str = m.group(1).replace(",", "").replace("،", "").strip()
    try:
        amt = Decimal(num_str)
    except Exception:
        return None, None
    if amt <= 0:
        return None, None
    rest = (m.group(2) or "").strip()
    return amt, rest if rest else None


def _parse_amount_and_optional_note(text: str):
    """
    تنسيقات مرنة:
    - «38 الفيروز» في سطر واحد
    - سطران: المبلغ ثم الملاحظة
    - «38» فقط (بدون ملاحظة من النص)
    """
    text = (text or "").strip()
    if not text:
        return None, None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None, None
    if len(lines) == 1:
        return _parse_single_line_amount_note(lines[0])
    first = lines[0]
    if _is_pure_amount_line(first):
        a = _parse_decimal_amount_token(first)
        if a is not None:
            note = "\n".join(lines[1:]).strip()
            return a, note if note else None
    a, rest_first = _parse_single_line_amount_note(first)
    if a is None:
        return None, None
    tail = lines[1:]
    if rest_first and tail:
        note = (rest_first + "\n" + "\n".join(tail)).strip()
    elif rest_first:
        note = rest_first
    elif tail:
        note = "\n".join(tail).strip()
    else:
        note = None
    return a, note if note else None


# مفاتيح حالة إدخال معاملة العميل (للتنظيف عند الإنهاء)
_CUST_TXN_KEYS = (
    "cust_txn_kind",
    "cust_txn_cid",
    "cust_txn_amount",
    "cust_txn_note_text",
    "cust_txn_photo_file_id",
)


def _is_public_http_url(url: str) -> bool:
    """يتحقق أن الرابط HTTP/HTTPS وقابل للاستخدام من خارج السيرفر."""
    if not url:
        return False
    try:
        p = urlparse(url.strip())
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if not host:
        return False
    # localhost / شبكات داخلية لا تعمل للمستخدمين الخارجيين
    private_hosts = {"localhost", "127.0.0.1", "0.0.0.0"}
    if host in private_hosts:
        return False
    return True


async def menu_customer_categories(update: Update, context: ContextTypes.DEFAULT_TYPE, back_customer_id: int):
    """عرض أصناف الصنف + إضافة/مسح"""
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
    context.user_data.pop("in_cust_cat_add_flow", None)
    context.user_data["in_cust_cat_add_flow"] = True
    context.user_data.pop("cust_cat_add_name", None)
    context.user_data.pop("cust_cat_add_kind", None)
    await query.edit_message_text(
        "أرسل اسم الصنف الجديد:",
        reply_markup=_kb_cust_cat_back(context),
    )
    return CAT_ADD_NAME


async def cust_cat_name_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text(
            "يرجى إرسال اسم صحيح للصنف.",
            reply_markup=_kb_cust_cat_back(context),
        )
        return CAT_ADD_NAME
    context.user_data["cust_cat_add_name"] = name

    keyboard = [
        [
            InlineKeyboardButton("🟢 أعطيت (gave)", callback_data="cust_cat_kind_gave"),
            InlineKeyboardButton("🔴 أخذت (took)", callback_data="cust_cat_kind_took"),
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
        context.user_data.pop("in_cust_cat_add_flow", None)
        await query.edit_message_text(
            "انتهت الجلسة. ابدأ من جديد.",
            reply_markup=_kb_cust_cat_back(context),
        )
        return ConversationHandler.END

    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            context.user_data.pop("in_cust_cat_add_flow", None)
            await query.edit_message_text("غير مسموح.", reply_markup=kb_main_menu())
            return ConversationHandler.END
        db.add(CustomerCategory(user_id=user.id, name=name, kind="took"))
        db.commit()
    finally:
        db.close()

    # رجوع للقائمة
    await menu_customer_categories(update, context, int(back_cid))
    context.user_data.pop("cust_cat_add_name", None)
    context.user_data.pop("in_cust_cat_add_flow", None)
    return ConversationHandler.END


async def cust_cat_kind_gave_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    back_cid = context.user_data.get("cust_cat_back_customer_id")
    name = context.user_data.get("cust_cat_add_name")
    if not back_cid or not name:
        context.user_data.pop("in_cust_cat_add_flow", None)
        await query.edit_message_text(
            "انتهت الجلسة. ابدأ من جديد.",
            reply_markup=_kb_cust_cat_back(context),
        )
        return ConversationHandler.END

    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            context.user_data.pop("in_cust_cat_add_flow", None)
            await query.edit_message_text("غير مسموح.", reply_markup=kb_main_menu())
            return ConversationHandler.END
        db.add(CustomerCategory(user_id=user.id, name=name, kind="gave"))
        db.commit()
    finally:
        db.close()

    await menu_customer_categories(update, context, int(back_cid))
    context.user_data.pop("cust_cat_add_name", None)
    context.user_data.pop("in_cust_cat_add_flow", None)
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
        "⚠️ هل أنت متأكد من حذف هذا الصنف؟\nلا يمكن التراجع.",
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
            await query.edit_message_text("غير مسموح.", reply_markup=kb_main_menu())
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


def _customers_ordered_by_usage_least_first(
    db, user_id: int, *, name_ilike: str | None = None, limit: int | None = None
):
    """
    قائمة عملاء المستخدم: الأقل معاملاتاً أولاً (أعلى الشاشة)، الأكثر معاملاتاً آخراً (أسفل).
    """
    txn_n = (
        db.query(
            CustomerTransaction.customer_id.label("cid"),
            func.count(CustomerTransaction.id).label("n"),
        )
        .group_by(CustomerTransaction.customer_id)
        .subquery()
    )
    q = (
        db.query(Customer)
        .outerjoin(txn_n, Customer.id == txn_n.c.cid)
        .filter(Customer.user_id == user_id)
        .order_by(func.coalesce(txn_n.c.n, 0).asc(), Customer.id.asc())
    )
    if name_ilike is not None:
        q = q.filter(Customer.name.ilike(f"%{name_ilike}%"))
    if limit is not None:
        q = q.limit(limit)
    return q.all()


def _cust_row_buttons(c: Customer) -> list[InlineKeyboardButton]:
    """مبلغ يسار، اسم يمين (عرض تيليجرام). داخل زر الاسم: محاذاة بداية النص عبر LRM."""
    bal, _, _ = _balance(c)
    emo = _balance_status_emoji(bal)
    name_label = f"\u200e{emo} {c.name}"[:40]
    amount_label = f"{bal:.2f} د.ع."
    cid = c.id
    return [
        InlineKeyboardButton(amount_label, callback_data=f"cust_{cid}"),
        InlineKeyboardButton(name_label, callback_data=f"cust_{cid}"),
    ]


async def reply_customer_search_results(
    update: Update, context: ContextTypes.DEFAULT_TYPE, q: str
) -> None:
    """نتائج بحث العملاء بنفس منطق البحث من دفتر الديون (رسالة جديدة)."""
    msg = update.effective_message
    if not msg:
        return
    if not q:
        await msg.reply_text(
            "اكتب نص بحث صحيح.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀ رجوع", callback_data="cust_search_back")]]
            ),
        )
        return
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await msg.reply_text(
                "يجب تسجيل الدخول أولاً. استخدم /start",
                reply_markup=kb_main_menu(),
            )
            return
        matches = _customers_ordered_by_usage_least_first(
            db, user.id, name_ilike=q, limit=25
        )
        if not matches:
            context.user_data["pending_add_name"] = q.strip()
            kb = [
                [InlineKeyboardButton("➕ إضافة كعميل بهذا الاسم", callback_data="cust_add_pending")],
                [InlineKeyboardButton("🔁 بحث جديد", callback_data="cust_search_start")],
                [InlineKeyboardButton("◀ قائمة العملاء", callback_data="menu_customers")],
            ]
            await msg.reply_text(
                "لا يوجد عملاء مطابقون لهذا البحث.",
                reply_markup=InlineKeyboardMarkup(kb),
            )
            return

        kb = []
        lines = [f"نتائج البحث: {q}"]
        for c in matches:
            bal, _, _ = _balance(c)
            emo = _balance_status_emoji(bal)
            lines.append(f"• {emo} {c.name} ({bal:.2f})")
            kb.append(_cust_row_buttons(c))
        kb.append([InlineKeyboardButton("🔁 بحث جديد", callback_data="cust_search_start")])
        kb.append([InlineKeyboardButton("◀ قائمة العملاء", callback_data="menu_customers")])
        await msg.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))
    finally:
        db.close()


async def cust_search_global_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بحث بالاسم من أي مكان (بدون فتح دفتر الديون) — يعمل فقط خارج محادثات أخرى."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text:
        return
    # إذا المستخدم داخل محادثة إضافة صنف، لا نسمح لمعالج البحث العام أن يرد
    # حتى لا تظهر رسائل بحث/إضافة كعميل أثناء إدخال اسم الصنف.
    if context.user_data.get("in_cust_cat_add_flow"):
        return
    # أثناء مسارات المصادقة/الاستعادة لا نتعامل مع النص العام:
    # هذا يمنع رمز التحقق (أرقام فقط مثل 305245) من أن يُفسَّر كمبلغ سريع.
    auth_like_keys = (
        "auth_action",
        "login_phone",
        "forgot_phone",
        "forgot_reset_user_id",
        "forgot_new_pwd",
        "chpwd_old_ok",
        "chpwd_new",
    )
    if any(context.user_data.get(k) is not None for k in auth_like_keys):
        return
    if context.user_data.get("last_menu") == "ledger":
        await update.message.reply_text(
            "لتسجيل مبلغ (مثل الراتب):\n"
            "① من رسالة «الدخل والمصروف» اضغط الصنف (مثلاً «راتبك الثابت»).\n"
            "② ثم أرسل المبلغ رقماً فقط.\n\n"
            "كتابة نص هنا لا تُسجَّل كقيد — لازم تختار الصنف أولاً.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("◀ الدخل والمصروف", callback_data="menu_ledger")],
                    [InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")],
                ]
            ),
        )
        return
    if _looks_like_phone_not_amount(text):
        await update.message.reply_text(
            "يبدو أن هذا رقم هاتف وليس مبلغاً للبحث بالاسم.\n"
            "لإضافة عميل برقم: افتح «دفتر الديون» ← إضافة عميل.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀ دفتر الديون", callback_data="menu_customers")]]
            ),
        )
        return
    if _is_pure_amount_line(text):
        amt = _parse_decimal_amount_token(text)
        if amt is not None:
            context.user_data["quick_amount"] = amt
            await update.message.reply_text(
                "ما هذا الرقم؟ هل هو مبلغ؟",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("🟢 أعطيت", callback_data="qamt_k_gave"),
                            InlineKeyboardButton("🔴 أخذت", callback_data="qamt_k_took"),
                        ],
                        [InlineKeyboardButton("❌ إلغاء", callback_data="qamt_cancel")],
                    ]
                ),
            )
            return
    await reply_customer_search_results(update, context, text)


async def qamt_kind_took_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if context.user_data.get("quick_amount") is None:
        await query.edit_message_text(
            "انتهت الجلسة. أرسل الرقم من جديد.",
            reply_markup=kb_main_menu(),
        )
        return
    context.user_data["quick_flow_kind"] = "took"
    await _edit_quick_customer_picker(query, context)


async def qamt_kind_gave_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if context.user_data.get("quick_amount") is None:
        await query.edit_message_text(
            "انتهت الجلسة. أرسل الرقم من جديد.",
            reply_markup=kb_main_menu(),
        )
        return
    context.user_data["quick_flow_kind"] = "gave"
    await _edit_quick_customer_picker(query, context)


def _clear_quick_amount_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("quick_amount", None)
    context.user_data.pop("quick_flow_kind", None)


async def qamt_cancel_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _clear_quick_amount_flow(context)
    await query.edit_message_text("تم الإلغاء.", reply_markup=kb_main_menu())


async def _edit_quick_customer_picker(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = SessionLocal()
    try:
        user = get_current_user(db, query.from_user.id)
        if not user:
            await query.edit_message_text(
                "يجب تسجيل الدخول أولاً.",
                reply_markup=kb_main_menu(),
            )
            _clear_quick_amount_flow(context)
            return
        customers = _customers_ordered_by_usage_least_first(
            db, user.id, limit=40
        )
        amt = context.user_data.get("quick_amount")
        kind = context.user_data.get("quick_flow_kind")
        if amt is None or kind not in ("took", "gave"):
            await query.edit_message_text(
                "انتهت الجلسة.",
                reply_markup=kb_main_menu(),
            )
            _clear_quick_amount_flow(context)
            return
        if not customers:
            _clear_quick_amount_flow(context)
            await query.edit_message_text(
                "لا يوجد عملاء بعد.\nأضف عميلاً من دفتر الديون أولاً.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("◀ دفتر الديون", callback_data="menu_customers")]]
                ),
            )
            return
        kind_ar = "أخذت 🔴" if kind == "took" else "أعطيت 🟢"
        lines = [
            f"{kind_ar}",
            f"المبلغ: {amt} د.ع.",
            "",
            "لأي عميل هذا المبلغ؟ اختر من الأزرار:",
        ]
        kb = []
        for c in customers:
            bal, _, _ = _balance(c)
            emo = _balance_status_emoji(bal)
            label = f"\u200e{emo} {c.name}"[:58]
            kb.append([InlineKeyboardButton(label, callback_data=f"qamt_pick_{c.id}")])
        kb.append([InlineKeyboardButton("❌ إلغاء", callback_data="qamt_cancel")])
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))
    finally:
        db.close()


async def quick_txn_pick_customer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختيار عميل بعد تحديد المبلغ والنوع — يدخل خطوة الملاحظة."""
    query = update.callback_query
    await query.answer()
    try:
        cid = int(query.data.replace("qamt_pick_", ""))
    except ValueError:
        await query.edit_message_text("خطأ.", reply_markup=kb_menu_customers())
        return ConversationHandler.END
    amt = context.user_data.pop("quick_amount", None)
    kind = context.user_data.pop("quick_flow_kind", None)
    if amt is None or kind not in ("took", "gave"):
        await query.edit_message_text(
            "انتهت الجلسة.",
            reply_markup=kb_menu_customers(),
        )
        return ConversationHandler.END
    db = SessionLocal()
    cust_name = ""
    try:
        cust = db.query(Customer).filter(Customer.id == cid).first()
        user = get_current_user(db, update.effective_user.id)
        if not cust or not user or cust.user_id != user.id:
            await query.edit_message_text(
                "غير مسموح أو العميل غير موجود.",
                reply_markup=kb_menu_customers(),
            )
            return ConversationHandler.END
        cust_name = cust.name
    finally:
        db.close()
    context.user_data["cust_txn_cid"] = cid
    context.user_data["cust_txn_kind"] = kind
    context.user_data["cust_txn_amount"] = amt
    context.user_data.pop("cust_txn_note_text", None)
    context.user_data.pop("cust_txn_photo_file_id", None)
    kind_label = "أخذت 🔴" if kind == "took" else "أعطيت 🟢"
    keyboard = [
        [InlineKeyboardButton("⏭️ تخطي الملاحظة", callback_data="cust_note_skip_btn")],
        [
            InlineKeyboardButton("↩ رجوع لتعديل السعر", callback_data="cust_txn_back_amount"),
            InlineKeyboardButton("◀ رجوع لقائمة العملاء", callback_data="cust_txn_exit"),
        ],
    ]
    await query.edit_message_text(
        f"{kind_label}\n"
        f"العميل: {cust_name}\n"
        f"المبلغ: {amt} د.ع.\n\n"
        "أرسل ملاحظة نصاً أو صورة.\n"
        "يمكنك «تخطي الملاحظة» إن لم تكن هناك ملاحظة.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CUST_NOTE


async def cust_add_from_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة عميل بالاسم المحفوظ من نتائج بحث فارغة."""
    query = update.callback_query
    await query.answer()
    name = context.user_data.pop("pending_add_name", None)
    if not name or not str(name).strip():
        await query.edit_message_text(
            "انتهت الجلسة. أعد البحث ثم اضغط «إضافة كعميل بهذا الاسم».",
            reply_markup=kb_menu_customers(),
        )
        return ConversationHandler.END
    context.user_data["cust_name"] = name.strip()
    await query.edit_message_text(
        f"إضافة عميل: {name.strip()}\n\n"
        "أرسل رقم هاتف العميل (اختياري).\n"
        "أو اضغط تخطي.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⏭️ تخطي الرقم", callback_data="cust_phone_skip_btn")]]
        ),
    )
    return CUST_PHONE


async def menu_customers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة دفتر الديون: إضافة عميل + قائمة العملاء"""
    query = update.callback_query
    await query.answer()
    context.user_data["last_menu"] = "customers"
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        if not user:
            await query.edit_message_text(
                "يجب تسجيل الدخول أولاً. استخدم /start",
                reply_markup=kb_main_menu(),
            )
            return
        customers = _customers_ordered_by_usage_least_first(db, user.id)
        keyboard: list[list[InlineKeyboardButton]] = []
        total_out = 0.0  # أعطيت (gave)
        total_in = 0.0   # أخذت (took)
        for c in customers:
            for t in c.transactions:
                amt = float(t.amount or 0)
                if t.kind == "gave":
                    total_out += amt
                else:
                    total_in += amt
            keyboard.append(_cust_row_buttons(c))
        keyboard.append([InlineKeyboardButton("➕ إضافة عميل", callback_data="cust_add")])
        keyboard.append([InlineKeyboardButton("🔎 بحث", callback_data="cust_search_start")])
        keyboard.append([InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")])
        remain = total_out - total_in
        # نفس منطق الموقع: أعطيت 🟢 / أخذت 🔴 / المجموع بلون حسب الإشارة (أعطيت − أخذت)
        if remain > 0:
            net_emoji = "🟢"
        elif remain < 0:
            net_emoji = "🔴"
        else:
            net_emoji = "⚪"
        await query.edit_message_text(
            (
                "دفتر الديون 📒\n\n"
                f"🟢 أعطيت: {total_out:.2f} د.ع.\n"
                f"🔴 أخذت: {total_in:.2f} د.ع.\n"
                f"{net_emoji} المجموع: {remain:.2f} د.ع.\n\n"
                "اختر عميلاً أو أضف عميلاً."
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def cust_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("◀ رجوع", callback_data="cust_search_back")]]
    await query.edit_message_text(
        "بحث العملاء 🔎\n\n"
        "اكتب اسم العميل أو جزء من الاسم:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CUST_SEARCH_QUERY


async def cust_search_query_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = (update.message.text or "").strip()
    if not q:
        await update.message.reply_text(
            "اكتب نص بحث صحيح.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀ رجوع", callback_data="cust_search_back")]]
            ),
        )
        return CUST_SEARCH_QUERY
    await reply_customer_search_results(update, context, q)
    return ConversationHandler.END


async def cust_search_back_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await menu_customers(update, context)
    return ConversationHandler.END


async def cust_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "إضافة عميل 📝\n\n"
        "أرسل اسم العميل (إجباري).\n\n"
        "يمكنك:\n"
        "• الاسم والرقم في سطر واحد (مثال: أحمد 07701234567)\n"
        "• أو سطرين: الاسم ثم الرقم تحته\n"
        "• أو الاسم الآن والرقم في رسالة لاحقة",
        reply_markup=kb_menu_customers(),
    )
    return CUST_NAME


async def cust_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    name_part, phone_raw = _parse_name_and_phone_from_text(raw)
    if not name_part:
        await update.message.reply_text(
            "يرجى إرسال اسم العميل.\n"
            "إذا أرسلت الرقم وحده، اكتب الاسم معه أو في الرسالة التالية.",
            reply_markup=kb_menu_customers(),
        )
        return CUST_NAME
    context.user_data["cust_name"] = name_part
    if phone_raw is not None:
        phone_norm = normalize_phone(phone_raw)
        if not is_plausible_iraq_mobile(phone_norm):
            await update.message.reply_text(
                "الرقم بجانب الاسم غير صحيح.\n"
                "جرّب: 077… أو 7××× أو +964… أو أرسل الاسم فقط ثم الرقم في رسالة أخرى.",
                reply_markup=kb_menu_customers(),
            )
            return CUST_NAME
        if await _save_new_customer_from_add_flow(update, context, name_part, phone_norm):
            context.user_data.pop("cust_name", None)
        return ConversationHandler.END

    await update.message.reply_text(
        "تم ✅\n\nأرسل رقم هاتف العميل (اختياري).\n"
        "إذا تريد تخطي الرقم اضغط زر التخطي.",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("⏭️ تخطي الرقم", callback_data="cust_phone_skip_btn")],
                [InlineKeyboardButton("◀ دفتر الديون", callback_data="menu_customers")],
            ]
        ),
    )
    return CUST_PHONE


async def cust_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.contact:
        raw = update.message.contact.phone_number or ""
    else:
        raw = (update.message.text or "").strip()
        _, extracted = _parse_name_and_phone_from_text(raw)
        if extracted and is_plausible_iraq_mobile(normalize_phone(extracted)):
            raw = extracted
    phone = normalize_phone(raw)
    if not is_plausible_iraq_mobile(phone):
        await update.message.reply_text(
            "رقم غير صحيح. جرّب: 077… أو 7××× أو +964… أو اضغط تخطي.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("⏭️ تخطي الرقم", callback_data="cust_phone_skip_btn")],
                    [InlineKeyboardButton("◀ دفتر الديون", callback_data="menu_customers")],
                ]
            ),
        )
        return CUST_PHONE
    name = context.user_data.get("cust_name")
    if not name:
        await update.message.reply_text(
            "انتهت الجلسة. ابدأ إضافة عميل من جديد.",
            reply_markup=kb_menu_customers(),
        )
        return ConversationHandler.END
    if await _save_new_customer_from_add_flow(update, context, name, phone):
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
            await query.edit_message_text(
                "انتهت الجلسة. استخدم /start",
                reply_markup=kb_main_menu(),
            )
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
    if bal > 0:
        # تحسين الوضوح: نخلي "الرصيد الحالي" كسطر مستقل تحت اسم العميل.
        balance_text = f"🟢\n📌 الرصيد الحالي: {bal:.2f} {cur}"
    elif bal < 0:
        balance_text = f"🔴\n📌 الرصيد الحالي: {bal:.2f} {cur}"
    else:
        balance_text = f"⚪\n📌 الرصيد الحالي: {bal:.2f} {cur}"

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
        + "\nآخر المعاملات:"
    )

    has_more = offset + TX_PAGE_SIZE < total
    plink = (
        db.query(PartnerLink)
        .filter(
            PartnerLink.status == "accepted",
            (PartnerLink.inviter_customer_id == cust.id) | (PartnerLink.invitee_customer_id == cust.id),
        )
        .first()
    )
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

    # زر أعطيت (يمين في RTL) ثم أخذت (يسار)
    keyboard.append(
        [
            InlineKeyboardButton("🟢 أعطيت", callback_data=f"cust_gave_{cust.id}"),
            InlineKeyboardButton("🔴 أخذت", callback_data=f"cust_took_{cust.id}"),
        ]
    )

    # عرض الباقيات بجانب تعديل الحساب (+ إرسال تحديثات إن وُجد ربط)
    edit_btn = InlineKeyboardButton("✏️ تعديل الحساب", callback_data=f"cust_edit_{cust.id}")
    share_btn = InlineKeyboardButton("📤 مشاركة", callback_data=f"cust_share_{cust.id}")
    send_upd_btn = InlineKeyboardButton("📤 إرسال التحديثات", callback_data=f"cust_partner_send_{cust.id}")
    if has_more:
        more_btn = InlineKeyboardButton(
            "➕ عرض الباقيات",
            callback_data=f"cust_tx_more_{cust.id}_{offset + TX_PAGE_SIZE}",
        )
        keyboard.append([more_btn])
        if plink:
            keyboard.append([edit_btn, send_upd_btn])
            keyboard.append([share_btn])
        else:
            keyboard.append([edit_btn, share_btn])
    else:
        if plink:
            keyboard.append([edit_btn, send_upd_btn])
            keyboard.append([share_btn])
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
            await update.callback_query.edit_message_text(
                "العميل غير موجود.",
                reply_markup=kb_menu_customers(),
            )
            return
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await update.callback_query.edit_message_text(
                "غير مسموح.",
                reply_markup=kb_main_menu(),
            )
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


def _local_input_file_for_web_photo(p, name: str):
    try:
        from telegram import FSInputFile

        return FSInputFile(p)
    except ImportError:
        return InputFile(BytesIO(p.read_bytes()), filename=name)


def _web_tx_public_photo_url(photo_file_id: str) -> str | None:
    """رابط مباشر لمعاينة صورة معاملة الموقع (يعمل من المتصفح ومن تيليجرام)."""
    s = str(photo_file_id)
    if not s.startswith("web:"):
        return None
    base = public_web_base_url_for_telegram_fetch()
    if not base:
        return None
    return f"{base}/creditbook/photo/{quote(s, safe='')}"


def _web_tx_local_path_name(photo_file_id: str) -> tuple | None:
    from creditbook_web_actions import is_safe_web_photo_name

    s = str(photo_file_id)
    if not s.startswith("web:"):
        return None
    name = s[4:]
    if not is_safe_web_photo_name(name):
        return None
    p = WEB_TX_UPLOAD_DIR / name
    if not p.is_file():
        return None
    return p, name


def _photo_args_for_telegram_send(photo_file_id: str | None, photo_web_blob: bytes | None = None) -> list:
    """قائمة مرشّحات لـ send_photo: file_id، أو بايتات من DB، أو رابط HTTPS، أو قرص."""
    if not photo_file_id:
        return []
    s = str(photo_file_id)
    if not s.startswith("web:"):
        return [s]
    name = s[4:]
    from creditbook_web_actions import is_safe_web_photo_name

    if not is_safe_web_photo_name(name):
        return []
    p = WEB_TX_UPLOAD_DIR / name
    out = []
    wb = photo_web_blob
    if wb is not None and len(wb) > 0:
        out.append(InputFile(BytesIO(bytes(wb)), filename=name))
    url = _web_tx_public_photo_url(s)
    if url:
        out.append(url)
    if p.is_file():
        out.append(_local_input_file_for_web_photo(p, name))
    return out


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
            InlineKeyboardButton(f"🔁 النوع: {icon} {kind_text}", callback_data=f"cust_tx_toggle_kind_{tx.id}"),
            InlineKeyboardButton("🗑 حذف", callback_data=f"cust_tx_delete_req_{tx.id}"),
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
            await query.edit_message_text(
                "المعاملة غير موجودة.",
                reply_markup=kb_menu_customers(),
            )
            return
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text(
                "غير مسموح.",
                reply_markup=kb_main_menu(),
            )
            return
        text, keyboard = await _render_tx_detail(db, tx)
        # نعرض التفاصيل أولاً، ثم الأزرار برسالة منفصلة (حسب طلب المستخدم).
        photo_args = _photo_args_for_telegram_send(
            getattr(tx, "photo_file_id", None),
            getattr(tx, "photo_web_blob", None),
        )
        _cap = text
        if len(_cap) > 1024:
            _cap = _cap[:1022] + "…"
        sent_photo = False
        for photo_arg in photo_args:
            try:
                await context.bot.send_photo(
                    chat_id=update.effective_user.id,
                    photo=photo_arg,
                    caption=_cap,
                )
                sent_photo = True
                break
            except Exception:
                continue
        # أحياناً تيليجرام يرفض send_photo لكن يقبل الملف كمستند
        if not sent_photo:
            pfi_s = str(tx.photo_file_id or "")
            wb = getattr(tx, "photo_web_blob", None)
            if wb is not None and len(wb) > 0 and pfi_s.startswith("web:"):
                fname = pfi_s[4:]
                try:
                    await context.bot.send_document(
                        chat_id=update.effective_user.id,
                        document=InputFile(BytesIO(bytes(wb)), filename=fname),
                        caption=_cap,
                    )
                    sent_photo = True
                except Exception:
                    pass
            if not sent_photo:
                pmeta = _web_tx_local_path_name(pfi_s)
                if pmeta:
                    plocal, fname = pmeta
                    try:
                        await context.bot.send_document(
                            chat_id=update.effective_user.id,
                            document=InputFile(BytesIO(plocal.read_bytes()), filename=fname),
                            caption=_cap,
                        )
                        sent_photo = True
                    except Exception:
                        pass
        if not sent_photo:
            pfi = getattr(tx, "photo_file_id", None)
            if pfi and str(pfi).startswith("web:"):
                vurl = _web_tx_public_photo_url(str(pfi))
                if vurl:
                    text = (
                        text
                        + "\n\n📎 لم يُعَدّ عرض الصورة داخل تيليجرام.\n"
                        + "افتح الرابط التالي لمعاينتها في المتصفح:\n"
                        + vurl
                    )
                else:
                    text = text + (
                        "\n\n⚠️ الصورة من الموقع: لم يُضبط رابط عام للموقع "
                        "(WEB_BASE_URL أو TELEGRAM_PHOTO_BASE_URL في Railway)."
                    )
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

        # نخلي رسالة الزر القديمة قصيرة مع زر رجوع للمعاملة
        try:
            await query.edit_message_text(
                "تم عرض تفاصيل المعاملة ✅",
                reply_markup=kb_tx_detail(tx_id),
            )
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
        "⚠️ هل أنت متأكد من حذف هذه المعاملة؟\nلا يمكن التراجع.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cust_tx_delete_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنفيذ الحذف فقط بعد زر «تأكيد الحذف» (cust_tx_delete_do_)."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if not data.startswith("cust_tx_delete_do_"):
        await query.edit_message_text(
            "استخدم زر تأكيد الحذف من الشاشة السابقة.",
            reply_markup=kb_main_menu(),
        )
        return
    tx_id = int(data.replace("cust_tx_delete_do_", ""))
    db = SessionLocal()
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await query.edit_message_text(
                "المعاملة غير موجودة.",
                reply_markup=kb_menu_customers(),
            )
            return
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text(
                "غير مسموح.",
                reply_markup=kb_main_menu(),
            )
            return
        db.delete(tx)
        db.commit()
        text, keyboard = await _build_customer_view(db, cust, offset=0)
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()


async def cust_tx_toggle_kind_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب تأكيد تغيير نوع المعاملة (أعطيت/أخذت)."""
    query = update.callback_query
    await query.answer()

    tx_id = int((query.data or "").replace("cust_tx_toggle_kind_", "", 1))
    db = SessionLocal()
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await query.edit_message_text(
                "المعاملة غير موجودة.",
                reply_markup=kb_menu_customers(),
            )
            return

        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text(
                "غير مسموح.",
                reply_markup=kb_main_menu(),
            )
            return

        current_kind_text = "أخذت" if tx.kind == "took" else "أعطيت"
        current_icon = _tx_kind_ar(tx.kind)
        new_kind = "gave" if tx.kind == "took" else "took"
        new_kind_text = "أخذت" if new_kind == "took" else "أعطيت"
        new_icon = _tx_kind_ar(new_kind)

        keyboard = [
            [
                InlineKeyboardButton(
                    f"✅ نعم: تغيير إلى {new_icon} {new_kind_text}",
                    callback_data=f"cust_tx_toggle_kind_do_{tx_id}",
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
            "تأكيد التغيير:\n"
            f"هذه المعاملة: {current_icon} {current_kind_text}\n"
            f"هل تريد تغييرها إلى: {new_icon} {new_kind_text}؟",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def cust_tx_toggle_kind_do_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنفيذ تغيير نوع المعاملة بعد التأكيد."""
    query = update.callback_query
    await query.answer()
    tx_id = int((query.data or "").replace("cust_tx_toggle_kind_do_", "", 1))

    db = SessionLocal()
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await query.edit_message_text("المعاملة غير موجودة.", reply_markup=kb_menu_customers())
            return

        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text("غير مسموح.", reply_markup=kb_main_menu())
            return

        tx.kind = "gave" if tx.kind == "took" else "took"
        db.commit()

        text, keyboard = await _render_tx_detail(db, tx)
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()


async def cust_tx_edit_back_to_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رجوع من تعديل مبلغ/ملاحظة إلى صفحة تفاصيل المعاملة."""
    query = update.callback_query
    await query.answer()
    try:
        tx_id = int((query.data or "").replace("cust_tx_", "", 1))
    except ValueError:
        from telegram.ext import ConversationHandler

        return ConversationHandler.END
    stored = context.user_data.get("tx_edit_id")
    if stored != tx_id:
        await query.answer("استخدم زر الرجوع لنفس المعاملة.", show_alert=True)
        return None
    context.user_data.pop("tx_edit_id", None)
    await cust_tx_detail(update, context, tx_id)
    from telegram.ext import ConversationHandler

    return ConversationHandler.END


async def cust_tx_edit_amount_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tx_id = int(query.data.replace("cust_tx_edit_amount_", ""))
    context.user_data["tx_edit_id"] = tx_id
    await query.edit_message_text(
        "أرسل المبلغ الجديد (رقم فقط مثال: 82.75):",
        reply_markup=kb_tx_detail(tx_id),
    )
    return TX_EDIT_AMOUNT


async def cust_tx_edit_amount_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tx_id = context.user_data.get("tx_edit_id")
    try:
        amount = Decimal((update.message.text or "").replace(",", "").strip())
        if amount <= 0:
            await update.message.reply_text(
                "أدخل مبلغاً أكبر من صفر.",
                reply_markup=kb_tx_detail(tx_id) if tx_id else kb_menu_customers(),
            )
            return TX_EDIT_AMOUNT
    except Exception:
        await update.message.reply_text(
            "أدخل رقماً صحيحاً.",
            reply_markup=kb_tx_detail(tx_id) if tx_id else kb_menu_customers(),
        )
        return TX_EDIT_AMOUNT

    db = SessionLocal()
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await update.message.reply_text(
                "المعاملة غير موجودة.",
                reply_markup=kb_tx_detail(tx_id) if tx_id else kb_menu_customers(),
            )
            return ConversationHandler.END
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await update.message.reply_text(
                "غير مسموح.",
                reply_markup=kb_tx_detail(tx_id) if tx_id else kb_menu_customers(),
            )
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
    await query.edit_message_text(
        "أرسل الملاحظة الجديدة (أو اكتب: حذف لحذفها):",
        reply_markup=kb_tx_detail(tx_id),
    )
    return TX_EDIT_NOTE


async def cust_tx_edit_note_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    note = None if raw.lower() in ("حذف", "delete") else raw

    db = SessionLocal()
    tx_id = context.user_data.get("tx_edit_id")
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await update.message.reply_text(
                "المعاملة غير موجودة.",
                reply_markup=kb_tx_detail(tx_id) if tx_id else kb_menu_customers(),
            )
            return ConversationHandler.END
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await update.message.reply_text(
                "غير مسموح.",
                reply_markup=kb_tx_detail(tx_id) if tx_id else kb_menu_customers(),
            )
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
    from handlers.datetime_picker import start_tx_datetime_pick

    return await start_tx_datetime_pick(update, context, tx_id)


async def apply_tx_datetime_from_picker(
    update: Update, context: ContextTypes.DEFAULT_TYPE, tx_id: int, dt: datetime
) -> int:
    """يُستدعى بعد اختيار التاريخ والوقت من لوحة الأزرار."""
    query = update.callback_query
    db = SessionLocal()
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            await query.edit_message_text(
                "المعاملة غير موجودة.",
                reply_markup=kb_menu_customers(),
            )
            return ConversationHandler.END
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text(
                "غير مسموح.",
                reply_markup=kb_main_menu(),
            )
            return ConversationHandler.END
        tx.created_at = dt
        db.commit()
        text, keyboard = await _render_tx_detail(db, tx)
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()
    context.user_data.pop("tx_edit_id", None)
    return ConversationHandler.END


def _tx_date_cb(tx_id: int, d: date) -> str:
    return f"txdt_{tx_id}_{d.strftime('%Y%m%d')}"


async def _apply_tx_new_date(
    update: Update, context: ContextTypes.DEFAULT_TYPE, tx_id: int, new_date: date
) -> int:
    """يطبّق التاريخ ويعيد ConversationHandler.END أو حالة الخطأ."""
    db = SessionLocal()
    try:
        tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
        if not tx:
            if update.message:
                await update.message.reply_text(
                    "المعاملة غير موجودة.",
                    reply_markup=kb_tx_detail(tx_id),
                )
            elif update.callback_query:
                await update.callback_query.edit_message_text(
                    "المعاملة غير موجودة.",
                    reply_markup=kb_tx_detail(tx_id),
                )
            return ConversationHandler.END
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            if update.message:
                await update.message.reply_text(
                    "غير مسموح.",
                    reply_markup=kb_tx_detail(tx_id),
                )
            elif update.callback_query:
                await update.callback_query.edit_message_text(
                    "غير مسموح.",
                    reply_markup=kb_tx_detail(tx_id),
                )
            return ConversationHandler.END
        old = tx.created_at
        if old:
            tx.created_at = datetime.combine(new_date, old.time())
        else:
            tx.created_at = datetime.combine(new_date, datetime.min.time())
        db.commit()
        text, keyboard = await _render_tx_detail(db, tx)
        if update.message:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        elif update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()
    context.user_data.pop("tx_edit_id", None)
    return ConversationHandler.END


async def cust_tx_edit_date_back_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    from handlers.datetime_picker import clear_dt_user_data

    clear_dt_user_data(context)
    context.user_data.pop("tx_edit_id", None)
    await query.edit_message_text("تم الرجوع.", reply_markup=kb_main_menu())
    return ConversationHandler.END


async def cust_tx_edit_date_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختيار تاريخ من زر بعد فشل التحليل."""
    query = update.callback_query
    await query.answer()
    m = re.match(r"^txdt_(\d+)_(\d{8})$", query.data or "")
    if not m:
        return TX_EDIT_DATE
    tx_id = int(m.group(1))
    ymd = m.group(2)
    try:
        new_date = datetime.strptime(ymd, "%Y%m%d").date()
    except ValueError:
        await query.edit_message_text("تاريخ غير صالح.", reply_markup=kb_main_menu())
        return TX_EDIT_DATE
    if context.user_data.get("tx_edit_id") != tx_id:
        await query.edit_message_text(
            "انتهت الجلسة. ابدأ من جديد.", reply_markup=kb_main_menu()
        )
        return ConversationHandler.END
    return await _apply_tx_new_date(update, context, tx_id, new_date)


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
            await query.edit_message_text(
                "المعاملة غير موجودة.",
                reply_markup=kb_tx_detail(tx_id),
            )
            return ConversationHandler.END
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text(
                "غير مسموح.",
                reply_markup=kb_tx_detail(tx_id),
            )
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
            await update.message.reply_text(
                "المعاملة غير موجودة.",
                reply_markup=kb_tx_detail(tx_id) if tx_id else kb_menu_customers(),
            )
            return ConversationHandler.END
        cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await update.message.reply_text(
                "غير مسموح.",
                reply_markup=kb_tx_detail(tx_id) if tx_id else kb_menu_customers(),
            )
            return ConversationHandler.END
        if not update.message.photo:
            await update.message.reply_text(
                "لم تصل صورة. حاول مرة أخرى.",
                reply_markup=kb_tx_detail(tx_id) if tx_id else kb_menu_customers(),
            )
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
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not user or not cust or cust.user_id != user.id:
            await query.edit_message_text(
                "يجب تسجيل الدخول أولاً (أو هذه العملية غير مسموحة).",
                reply_markup=kb_main_menu(),
            )
            context.user_data.pop("cust_txn_cid", None)
            context.user_data.pop("cust_txn_kind", None)
            return ConversationHandler.END
    finally:
        db.close()
    context.user_data["cust_txn_kind"] = "took"
    context.user_data["cust_txn_cid"] = cid
    # افتح الحاسبة تلقائياً مباشرة عند اختيار النوع.
    context.user_data["cust_calc_expr"] = "0"
    context.user_data["cust_calc_last_was_equals"] = False
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧮 إظهار الحاسبة", callback_data="calc_amt_open")],
            [
                InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_txn_back_{cid}"),
                InlineKeyboardButton("◀ رجوع لقائمة العملاء", callback_data="cust_txn_exit"),
            ],
        ]
    )
    await query.edit_message_text(
        "(أخذت 🔴)\n\n"
        "ارسل المبلغ\n"
        "او اضغط 🧮 الحاسبة لإظهارها\n"
        "وبعدها انقر على ✅ إدخال المبلغ",
        reply_markup=kb,
    )
    return CUST_AMOUNT


async def cust_gave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أعطيت (باللون الأخضر) — العميل مدين"""
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_gave_", ""))
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not user or not cust or cust.user_id != user.id:
            await query.edit_message_text(
                "يجب تسجيل الدخول أولاً (أو هذه العملية غير مسموحة).",
                reply_markup=kb_main_menu(),
            )
            context.user_data.pop("cust_txn_cid", None)
            context.user_data.pop("cust_txn_kind", None)
            return ConversationHandler.END
    finally:
        db.close()
    context.user_data["cust_txn_kind"] = "gave"
    context.user_data["cust_txn_cid"] = cid
    # افتح الحاسبة تلقائياً مباشرة عند اختيار النوع.
    context.user_data["cust_calc_expr"] = "0"
    context.user_data["cust_calc_last_was_equals"] = False
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧮 إظهار الحاسبة", callback_data="calc_amt_open")],
            [
                InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_txn_back_{cid}"),
                InlineKeyboardButton("◀ رجوع لقائمة العملاء", callback_data="cust_txn_exit"),
            ],
        ]
    )
    await query.edit_message_text(
        "(أعطيت 🟢)\n\n"
        "ارسل المبلغ\n"
        "او اضغط 🧮 الحاسبة لإظهارها\n"
        "وبعدها انقر على ✅ إدخال المبلغ",
        reply_markup=kb,
    )
    return CUST_AMOUNT


async def cust_txn_back_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رجوع لصفحة العميل وإنهاء إدخال المعاملة."""
    query = update.callback_query
    await query.answer()
    try:
        cid = int(query.data.replace("cust_txn_back_", ""))
    except Exception:
        await query.edit_message_text(
            "غير قادر على الرجوع.",
            reply_markup=kb_menu_customers(),
        )
        return ConversationHandler.END

    for k in _CUST_TXN_KEYS:
        context.user_data.pop(k, None)

    await customer_detail(update, context, cid, offset=0)
    return ConversationHandler.END


async def cust_txn_exit_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الخروج من إدخال المعاملة والعودة لقائمة العملاء."""
    query = update.callback_query
    await query.answer()
    for k in _CUST_TXN_KEYS:
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
        [InlineKeyboardButton("◀ رجوع لقائمة العملاء", callback_data="cust_txn_exit")],
    ]
    await _safe_edit_callback_text(
        query,
        "رجوع لتعديل السعر.\n\n"
        "أرسل المبلغ (رقم أو مبلغ+ملاحظة أو سطرين أو صورة بالتعليق).",
        keyboard,
    )
    return CUST_AMOUNT


async def cust_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    amount, note_opt = _parse_amount_and_optional_note(raw)
    if amount is None:
        await update.message.reply_text(
            "لم أستخرج مبلغاً صحيحاً.\n"
            "جرّب: 38  أو  38 الفيروز  أو سطرين (المبلغ ثم الملاحظة)\n"
            "أو أرسل صورة والمبلغ في تعليق الصورة.",
            reply_markup=_kb_cust_txn_flow(context.user_data.get("cust_txn_cid")),
        )
        return CUST_AMOUNT
    context.user_data["cust_txn_amount"] = amount
    if note_opt:
        context.user_data["cust_txn_note_text"] = note_opt
    else:
        context.user_data.pop("cust_txn_note_text", None)
    keyboard = [
        [
            InlineKeyboardButton(
                "✅ حفظ بدون صورة" if note_opt else "⏭️ تخطي الملاحظة",
                callback_data="cust_note_skip_btn",
            )
        ],
        [
            InlineKeyboardButton("↩ رجوع لتعديل السعر", callback_data="cust_txn_back_amount"),
            InlineKeyboardButton("◀ رجوع لقائمة العملاء", callback_data="cust_txn_exit"),
        ],
    ]
    if note_opt:
        kind_label = "أخذت 🔴" if context.user_data.get("cust_txn_kind") == "took" else "أعطيت 🟢"
        text = (
            f"{kind_label}\n\n"
            "لقد استلمت المبلغ والملاحظة ✅\n\n"
            "هل تريد إضافة صورة؟\n\n"
            "أرسل صورة الآن، أو اضغط «حفظ بدون صورة»."
        )
        # رد جديد بعد رسالة المستخدم (لا نعدّل رسالة التعليمات السابقة)
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.message.reply_text(
            "تم استلام المبلغ ✅\n\n"
            "أرسل ملاحظة أو صورة.\n"
            "يمكنك استخدام «تخطي الملاحظة» إن لم تكن هناك ملاحظة.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    return CUST_NOTE


async def cust_amount_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """صورة من خطوة المبلغ: المبلغ (والملاحظة) في تعليق الصورة."""
    if not update.message.photo:
        return CUST_AMOUNT
    caption = (update.message.caption or "").strip()
    if not caption:
        await update.message.reply_text(
            "أرسل الصورة مع تعليق يحتوي المبلغ.\n"
            "أمثلة للتعليق: 38  أو  38 الفيروز  أو سطرين (المبلغ ثم الملاحظة).",
            reply_markup=_kb_cust_txn_flow(context.user_data.get("cust_txn_cid")),
        )
        return CUST_AMOUNT
    amount, note_opt = _parse_amount_and_optional_note(caption)
    if amount is None:
        await update.message.reply_text(
            "لم أستخرج مبلغاً من تعليق الصورة. جرّب: 38  أو  38 الفيروز",
            reply_markup=_kb_cust_txn_flow(context.user_data.get("cust_txn_cid")),
        )
        return CUST_AMOUNT
    context.user_data["cust_txn_amount"] = amount
    context.user_data["cust_txn_photo_file_id"] = update.message.photo[-1].file_id
    if note_opt:
        context.user_data["cust_txn_note_text"] = note_opt
    else:
        context.user_data.pop("cust_txn_note_text", None)
    keyboard = [
        [
            InlineKeyboardButton(
                "✅ حفظ كما هو" if note_opt else "⏭️ تخطي الملاحظة",
                callback_data="cust_note_skip_btn",
            )
        ],
        [
            InlineKeyboardButton("↩ رجوع لتعديل السعر", callback_data="cust_txn_back_amount"),
            InlineKeyboardButton("◀ رجوع لقائمة العملاء", callback_data="cust_txn_exit"),
        ],
    ]
    kind_label = "أخذت 🔴" if context.user_data.get("cust_txn_kind") == "took" else "أعطيت 🟢"
    if note_opt:
        text = (
            f"{kind_label}\n\n"
            "لقد استلمت المبلغ والملاحظة والصورة ✅\n\n"
            "هل تريد تعديل الملاحظة بإرسال نص؟\n"
            "أو اضغط «حفظ كما هو»."
        )
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.message.reply_text(
            f"{kind_label}\n\n"
            "تم حفظ المبلغ والصورة ✅\n\n"
            "أرسل ملاحظة نصاً، أو «تخطي الملاحظة».",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    return CUST_NOTE


async def cust_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # نص الرسالة الحالية يطغى على الملاحظة المسجّلة مسبقاً (مثلاً من خطوة المبلغ)
    text_in = ((update.message.text or "").strip()) if update.message and update.message.text else ""
    prefilled = context.user_data.get("cust_txn_note_text")
    if text_in:
        note = text_in
    else:
        note = prefilled or ""
    db = SessionLocal()
    try:
        cid = context.user_data.get("cust_txn_cid")
        kind = context.user_data.get("cust_txn_kind")
        amount = context.user_data.get("cust_txn_amount")
        photo_file_id = context.user_data.get("cust_txn_photo_file_id")
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not cust:
            await update.message.reply_text(
                "العميل غير موجود.",
                reply_markup=kb_menu_customers(),
            )
            return ConversationHandler.END
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await update.message.reply_text(
                "غير مسموح.",
                reply_markup=kb_menu_customers(),
            )
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
        db.refresh(t)
        from handlers.partner_link import maybe_queue_partner_tx

        maybe_queue_partner_tx(db, t)
        text, keyboard = await _build_customer_view(db, cust, offset=0)
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
    for k in _CUST_TXN_KEYS:
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

    # ملاحظة مسجّلة مسبقاً + صورة بدون تعليق → احفظ معاً
    if context.user_data.get("cust_txn_note_text"):
        return await cust_note(update, context)

    cid = context.user_data.get("cust_txn_cid")
    keyboard = [
        [InlineKeyboardButton("⏭️ تخطي الملاحظة", callback_data="cust_note_skip_btn")],
        [
            InlineKeyboardButton("↩ رجوع لتعديل السعر", callback_data="cust_txn_back_amount"),
            InlineKeyboardButton("◀ رجوع لقائمة العملاء", callback_data="cust_txn_exit"),
        ],
    ]
    await update.message.reply_text(
        "تم استلام الصورة ✅\n\nالآن أرسل الملاحظة نصاً (أو اضغط تخطي).",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CUST_NOTE


async def cust_note_skip_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تخطي الملاحظة عبر زر بدل كتابة /skip"""
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
            await query.edit_message_text(
                "العميل غير موجود.",
                reply_markup=kb_menu_customers(),
            )
            return ConversationHandler.END
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text(
                "غير مسموح.",
                reply_markup=kb_menu_customers(),
            )
            return ConversationHandler.END
        note = context.user_data.get("cust_txn_note_text")
        t = CustomerTransaction(
            customer_id=cid,
            amount=amount,
            kind=kind,
            note=note or None,
            photo_file_id=photo_file_id,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        from handlers.partner_link import maybe_queue_partner_tx

        maybe_queue_partner_tx(db, t)
        text, keyboard = await _build_customer_view(db, cust, offset=0)
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
    for k in _CUST_TXN_KEYS:
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def cust_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    is_back = data.startswith("cust_edit_back_")
    if is_back:
        cid = int(data.replace("cust_edit_back_", "", 1))
        prefix = "تم الرجوع.\n\n"
    else:
        cid = int(data.replace("cust_edit_", "", 1))
        prefix = ""
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not cust:
            await query.edit_message_text(
                "العميل غير موجود.",
                reply_markup=kb_menu_customers(),
            )
            return
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text(
                "غير مسموح.",
                reply_markup=kb_main_menu(),
            )
            return
        plink = (
            db.query(PartnerLink)
            .filter(
                PartnerLink.status == "accepted",
                (PartnerLink.inviter_customer_id == cid) | (PartnerLink.invitee_customer_id == cid),
            )
            .first()
        )
        text = (
            prefix
            + f"تعديل: {cust.name}\n"
            + (f"الرقم: {cust.phone}" if cust.phone else "لا يوجد رقم")
        )
        keyboard = [
            [InlineKeyboardButton("تغيير الاسم", callback_data=f"cust_editname_{cid}")],
            [InlineKeyboardButton("تغيير الرقم", callback_data=f"cust_editphone_{cid}")],
        ]
        if plink:
            keyboard.append(
                [InlineKeyboardButton("📤 إرسال التحديثات", callback_data=f"cust_partner_send_{cid}")]
            )
            keyboard.append(
                [InlineKeyboardButton("🔗 فصل الربط مع عميل آخر", callback_data=f"cust_detach_link_{cid}")]
            )
        else:
            keyboard.append(
                [InlineKeyboardButton("🔗 ربط مع مستخدم آخر", callback_data=f"cust_partner_invite_{cid}")]
            )
        keyboard.append([InlineKeyboardButton("🔔 تذكيرات التسديد", callback_data=f"cust_reminder_{cid}")])
        keyboard.append([InlineKeyboardButton("🗑 حذف العميل", callback_data=f"cust_del_req_{cid}")])
        keyboard.append([InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()


async def cust_detach_link_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فصل الربط المقبول لهذا العميل عن الطرف الآخر."""
    query = update.callback_query
    await query.answer()
    cid = int((query.data or "").replace("cust_detach_link_", "", 1))
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.id == cid).first()
        user = get_current_user(db, update.effective_user.id)
        if not cust or not user or cust.user_id != user.id:
            await _safe_edit_callback_text(
                query,
                "غير مسموح.",
                [[InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")]],
            )
            return

        link = (
            db.query(PartnerLink)
            .filter(
                PartnerLink.status == "accepted",
                (PartnerLink.inviter_customer_id == cid) | (PartnerLink.invitee_customer_id == cid),
            )
            .first()
        )
        if not link:
            await _safe_edit_callback_text(
                query,
                "لا يوجد ربط مفعّل لهذا العميل.",
                [[InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")]],
            )
            return

        link.status = "cancelled"
        db.query(PartnerPendingTx).filter(
            PartnerPendingTx.partner_link_id == link.id, PartnerPendingTx.status == "pending"
        ).delete(synchronize_session=False)
        db.commit()

        await _safe_edit_callback_text(
            query,
            "تم فصل الربط ✅",
            [[InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")]],
        )
    finally:
        db.close()


async def cust_edit_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_editname_", ""))
    context.user_data["cust_edit_id"] = cid
    context.user_data["cust_edit_field"] = "name"
    await query.edit_message_text(
        "أرسل الاسم الجديد للعميل:",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ رجوع", callback_data=f"cust_edit_back_{cid}")]]
        ),
    )
    return CUST_EDIT_NAME


async def cust_edit_phone_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_editphone_", ""))
    context.user_data["cust_edit_id"] = cid
    context.user_data["cust_edit_field"] = "phone"
    await query.edit_message_text(
        "أرسل رقم الهاتف الجديد (أو اكتب: حذف لإزالة الرقم):",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ رجوع", callback_data=f"cust_edit_back_{cid}")]]
        ),
    )
    return CUST_EDIT_PHONE


async def cust_edit_name_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        cid = context.user_data.get("cust_edit_id")
        await update.message.reply_text(
            "أرسل اسماً صحيحاً.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀ رجوع", callback_data=f"cust_edit_back_{cid}")]]
            )
            if cid
            else kb_menu_customers(),
        )
        return CUST_EDIT_NAME
    cid = context.user_data.get("cust_edit_id")
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not user or not cust or cust.user_id != user.id:
            keyboard = [
                [InlineKeyboardButton("📝 إنشاء حساب", callback_data="auth_register")],
                [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="auth_login")],
                [InlineKeyboardButton("🔑 نسيت كلمة المرور", callback_data="auth_forgot")],
            ]
            await update.message.reply_text(
                "يجب تسجيل الدخول أولاً.",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return ConversationHandler.END
        cust.name = name
        db.commit()
        keyboard = [[InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")]]
        await update.message.reply_text(
            "تم تحديث الاسم ✅", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    finally:
        db.close()
    context.user_data.pop("cust_edit_id", None)
    context.user_data.pop("cust_edit_field", None)
    return ConversationHandler.END


async def cust_edit_phone_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.contact:
        raw = update.message.contact.phone_number or ""
    else:
        raw = (update.message.text or "").strip()
    phone = None if raw.lower() in ("حذف", "delete", "") else normalize_phone(raw)
    if phone is not None and not is_plausible_iraq_mobile(phone):
        cid = context.user_data.get("cust_edit_id")
        await update.message.reply_text(
            "رقم غير صحيح. أرسل الرقم أو اكتب: حذف",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀ رجوع", callback_data=f"cust_edit_back_{cid}")]]
            )
            if cid
            else kb_menu_customers(),
        )
        return CUST_EDIT_PHONE
    cid = context.user_data.get("cust_edit_id")
    db = SessionLocal()
    try:
        user = get_current_user(db, update.effective_user.id)
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not user or not cust or cust.user_id != user.id:
            keyboard = [
                [InlineKeyboardButton("📝 إنشاء حساب", callback_data="auth_register")],
                [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="auth_login")],
                [InlineKeyboardButton("🔑 نسيت كلمة المرور", callback_data="auth_forgot")],
            ]
            await update.message.reply_text(
                "يجب تسجيل الدخول أولاً.",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return ConversationHandler.END
        cust.phone = phone
        db.commit()
        keyboard = [[InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")]]
        await update.message.reply_text(
            "تم تحديث الرقم ✅" if phone else "تم حذف الرقم ✅",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()
    context.user_data.pop("cust_edit_id", None)
    context.user_data.pop("cust_edit_field", None)
    return ConversationHandler.END


async def cust_delete_req_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شاشة تأكيد قبل حذف العميل."""
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_del_req_", ""))
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not cust:
            await query.edit_message_text(
                "العميل غير موجود.",
                reply_markup=kb_menu_customers(),
            )
            return
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text(
                "غير مسموح.",
                reply_markup=kb_main_menu(),
            )
            return
        tx_count = db.query(CustomerTransaction).filter(CustomerTransaction.customer_id == cid).count()
        name = cust.name
    finally:
        db.close()
    keyboard = [
        [InlineKeyboardButton("✅ تأكيد الحذف", callback_data=f"cust_del_do_{cid}")],
        [InlineKeyboardButton("↩ تراجع", callback_data=f"cust_edit_{cid}")],
    ]
    extra = f"\n\nعدد المعاملات المرتبطة: {tx_count}" if tx_count else ""
    await query.edit_message_text(
        f"⚠️ حذف العميل «{name}»{extra}\n\n"
        "سيتم حذف جميع معاملاته وروابط المشاركة نهائياً.\n\n"
        "هل أنت متأكد؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cust_delete_do_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنفيذ حذف العميل بعد التأكيد."""
    query = update.callback_query
    await query.answer()
    cid = int(query.data.replace("cust_del_do_", ""))
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not cust:
            await _safe_edit_callback_text(
                query,
                "العميل غير موجود.",
                [[InlineKeyboardButton("◀ قائمة العملاء", callback_data="menu_customers")]],
            )
            return
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await _safe_edit_callback_text(
                query,
                "غير مسموح.",
                [[InlineKeyboardButton("◀ قائمة العملاء", callback_data="menu_customers")]],
            )
            return
        name = cust.name
        # PostgreSQL يرفض حذف العميل طالما توجد معاملات أو روابط مشاركة — نحذفها أولاً
        # إصلاح حذف العميل المرتبط: نفك أي روابط/انتظار/تذكيرات مرتبطة بهذا العميل.
        tx_ids = [r[0] for r in db.query(CustomerTransaction.id).filter(CustomerTransaction.customer_id == cid).all()]
        links = (
            db.query(PartnerLink)
            .filter((PartnerLink.inviter_customer_id == cid) | (PartnerLink.invitee_customer_id == cid))
            .all()
        )
        link_ids = [l.id for l in links]

        if link_ids:
            db.query(PartnerPendingTx).filter(
                PartnerPendingTx.partner_link_id.in_(link_ids)
            ).delete(synchronize_session=False)

        if tx_ids:
            db.query(PartnerPendingTx).filter(
                PartnerPendingTx.source_tx_id.in_(tx_ids)
            ).delete(synchronize_session=False)
            db.query(PartnerPendingTx).filter(
                PartnerPendingTx.mirrored_tx_id.in_(tx_ids)
            ).delete(synchronize_session=False)

        db.query(CustomerPaymentReminder).filter(
            CustomerPaymentReminder.customer_id == cid
        ).delete(synchronize_session=False)

        db.query(PartnerLink).filter(
            (PartnerLink.inviter_customer_id == cid) | (PartnerLink.invitee_customer_id == cid)
        ).delete(synchronize_session=False)

        db.commit()

        db.query(CustomerTransaction).filter(CustomerTransaction.customer_id == cid).delete(
            synchronize_session=False
        )
        db.query(ShareLink).filter(ShareLink.customer_id == cid).delete(synchronize_session=False)
        db.delete(cust)
        db.commit()
        keyboard = [[InlineKeyboardButton("◀ قائمة العملاء", callback_data="menu_customers")]]
        await _safe_edit_callback_text(query, f"تم حذف العميل: {name} ✅", keyboard)
    except Exception as e:
        db.rollback()
        await _safe_edit_callback_text(
            query,
            f"تعذّر حذف العميل.\n{str(e)[:200]}",
            [
                [InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")],
                [InlineKeyboardButton("◀ قائمة العملاء", callback_data="menu_customers")],
            ],
        )
    finally:
        db.close()


async def cust_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مشاركة: رسالة واتساب + رابط لرؤية المعاملات"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    try:
        cid = int(query.data.replace("cust_share_", ""))
    except Exception:
        await _safe_edit_callback_text(
            query,
            "تعذر فتح المشاركة. حاول مرة أخرى.",
            [[InlineKeyboardButton("◀ رجوع", callback_data="menu_customers")]],
        )
        return
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.id == cid).first()
        if not cust:
            await query.edit_message_text(
                "العميل غير موجود.",
                reply_markup=kb_menu_customers(),
            )
            return
        user = get_current_user(db, update.effective_user.id)
        if not user or cust.user_id != user.id:
            await query.edit_message_text(
                "غير مسموح.",
                reply_markup=kb_main_menu(),
            )
            return
        bal, gave, took = _balance(cust)
        cur = "د.ع."
        token = secrets.token_urlsafe(16)
        expires = datetime.utcnow() + timedelta(days=30)
        link = ShareLink(customer_id=cust.id, token=token, expires_at=expires)
        db.add(link)
        db.commit()
        # رابط عرض المعاملات:
        # 1) رابط موقع عام (WEB_BASE_URL أو استنتاج من RAILWAY_PUBLIC_DOMAIN)
        # 2) fallback تليجرام فقط إذا ماكو دومين عام نهائياً
        base = public_web_base_url_for_telegram_fetch() or (WEB_BASE_URL or "").strip().rstrip("/")
        if _is_public_http_url(base):
            view_url = f"{base}/creditbook/balance/{token}?lang=ar"
            using_web = True
        else:
            me = await context.bot.get_me()
            view_url = f"https://t.me/{me.username}?start=view_{token}"
            using_web = False
        if bal > 0:
            msg_balance = f"عليك رصيد {bal:.2f} {cur}"
        elif bal < 0:
            # النص يُرسل للطرف الآخر (العميل). استخدم صيغة "لك" بدل "لي".
            msg_balance = f"لك رصيد {abs(bal):.2f} {cur}"
        else:
            msg_balance = "الرصيد صفر"
        link_hint = "⬇️ المس الرابط لمشاهدة كافة التفاصيل"
        # نص واتساب/المعاينة للطرف الآخر: اسم العميل ثم الرصيد ثم الرابط فقط.
        share_text = (
            f"{cust.name}\n\n"
            f"{msg_balance}\n"
            "ــــــــــــــــــــــــ\n"
            f"{link_hint}\n"
            f"{view_url}"
        )
        # زر يفتح واتساب على محادثة رقم العميل مع النص جاهز
        # نخلي رابط الصفحة بسطر لوحده حتى واتساب يتعامل معه كرابط تلقائي.
        wa_text = share_text
        wa_num = cust.phone and wa_number(cust.phone)
        # زر واتساب يظهر دائمًا:
        # - مع رقم العميل: يفتح المحادثة معه
        # - بدون رقم: يفتح واتساب مع النص فقط (المستخدم يختار جهة الإرسال)
        if wa_num:
            wa_url = f"https://api.whatsapp.com/send?phone={wa_num}&text={quote(wa_text)}"
        else:
            wa_url = f"https://api.whatsapp.com/send?text={quote(wa_text)}"

        keyboard = [
            [InlineKeyboardButton("فتح صفحة المعاملات", url=view_url)],
            [InlineKeyboardButton("فتح واتساب وإرسال الرسالة", url=wa_url)],
            [InlineKeyboardButton("◀ رجوع للعميل", callback_data=f"cust_{cid}")],
        ]
        await _safe_edit_callback_text(
            query,
            (
                "مشاركة 📤\n\nاستخدم الأزرار أدناه:\n\n"
                + share_text
                + (
                    "\n\n⚠️ ملاحظة: لم يتم العثور على دومين ويب عام، لذلك الرابط احتياطي داخل تليجرام."
                    if not using_web
                    else ""
                )
            ),
            keyboard,
        )
    finally:
        db.close()


async def cust_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """توجيه callback: عمليات العملاء والمعاملات"""
    query = update.callback_query
    data = query.data
    if data == "cust_add_pending":
        await query.answer("أعد البحث ثم اضغط الإضافة من الرسالة الجديدة.", show_alert=True)
        return
    if data == "cust_add" or data == "noop":
        await query.answer()
        return

    # ربط الشركاء / إرسال تحديثات — يُستدعى من داخل محادثة المعاملة أيضاً
    if data.startswith("cust_partner_invite_"):
        from handlers.partner_link import partner_link_invite_start

        await partner_link_invite_start(update, context)
        return
    if data.startswith("cust_partner_send_"):
        from handlers.partner_link import partner_send_updates_click

        await partner_send_updates_click(update, context)
        return
    if data.startswith("cust_reminder_"):
        await query.answer("أنهِ إضافة المعاملة الحالية أولاً أو استخدم زر الرجوع.", show_alert=True)
        return

    if data.startswith("cust_detach_link_"):
        await cust_detach_link_click(update, context)
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
    if data.startswith("cust_tx_delete_do_"):
        await cust_tx_delete_click(update, context)
        return
    if data.startswith("cust_tx_toggle_kind_do_"):
        await cust_tx_toggle_kind_do_click(update, context)
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

    # حذف العميل: تأكيد ثم تنفيذ (قبل cust_edit_ حتى لا يختلط أي بادئة لاحقاً)
    if data.startswith("cust_del_do_"):
        await cust_delete_do_click(update, context)
        return
    if data.startswith("cust_del_req_"):
        await cust_delete_req_click(update, context)
        return
    if data.startswith("cust_edit_"):
        await cust_edit_menu(update, context)
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
