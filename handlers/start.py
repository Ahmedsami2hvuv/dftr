# -*- coding: utf-8 -*-
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import SessionLocal
from app_models import User, ShareLink
from config import ADMIN_ID


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    # ربط مستخدمين: /start plink_TOKEN
    if args and args[0].startswith("plink_"):
        token = args[0].replace("plink_", "", 1)
        from handlers.partner_link import handle_start_partner_link

        await handle_start_partner_link(update, context, token)
        return
    # رابط مشاركة: /start view_TOKEN
    if args and args[0].startswith("view_"):
        token = args[0].replace("view_", "", 1)
        db = SessionLocal()
        try:
            link = db.query(ShareLink).filter(ShareLink.token == token).first()
            if not link or (link.expires_at and link.expires_at < datetime.utcnow()):
                await update.message.reply_text(
                    "رابط غير صالح أو منتهي الصلاحية.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")]]
                    ),
                )
                return
            cust = link.customer
            gave = sum(t.amount for t in cust.transactions if t.kind == "gave")
            took = sum(t.amount for t in cust.transactions if t.kind == "took")
            bal = float(gave - took)
            cur = "د.ع."
            lines = [f"معاملات مرتبطة برقم العميل: {cust.name}", f"الرصيد: {bal:.2f} {cur}\n"]
            for t in cust.transactions[:50]:
                kind_ar = "أعطيت" if t.kind == "gave" else "أخذت"
                lines.append(f"{t.created_at.strftime('%Y-%m-%d %H:%M')} — {kind_ar}: {t.amount} {cur}" + (f" — {t.note}" if t.note else ""))
            if len(cust.transactions) > 50:
                lines.append(f"\n... و {len(cust.transactions) - 50} معاملة أخرى")
            await update.message.reply_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")]]
                ),
            )
        finally:
            db.close()
        return

    text = (
        "مرحباً بك في دفتر الحسابات 📒\n\n"
        "يمكنك تسجيل حساب جديد أو الدخول إلى حسابك."
    )
    db = SessionLocal()
    try:
        tid = int(update.effective_user.id)
        user = db.query(User).filter(User.telegram_id == tid).first()
        if user:
            context.user_data["last_menu"] = "main"
            text = (
                f"مرحباً مجدداً، {user.full_name or user.username or 'صديقي'} 👋\n\n"
                "استخدم القائمة أدناه لإدارة دفترك.\n"
                "أو اكتب أي جزء من اسم العميل للبحث عنه مباشرة."
            )
            keyboard = [
                [InlineKeyboardButton("📒 دفتر الديون", callback_data="menu_customers")],
                [InlineKeyboardButton("📒 الدخل والمصروف", callback_data="menu_ledger")],
                [InlineKeyboardButton("👤 حسابي", callback_data="menu_profile")],
                [InlineKeyboardButton("🧾 طريقة الاستخدام", callback_data="usage_instructions")],
            ]
            if update.effective_user.id == ADMIN_ID:
                keyboard.append([InlineKeyboardButton("🔐 لوحة الأدمن", callback_data="admin_panel")])
        else:
            # غير مربوط بالحساب: صفّر أي حالة محادثة عالقة (مثلاً بعد تسجيل خروج)
            context.user_data.clear()
            keyboard = [
                [InlineKeyboardButton("📝 إنشاء حساب", callback_data="auth_register")],
                [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="auth_login")],
                [InlineKeyboardButton("🔑 نسيت كلمة المرور", callback_data="auth_forgot")],
            ]
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        db.close()


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """القائمة الرئيسية للمستخدم المسجل"""
    query = update.callback_query
    await query.answer()

    # حماية: إذا المستخدم غير مسجل (بعد تسجيل خروج أو من زر قديم) لا نعرض القائمة.
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == update.effective_user.id).first()
        if not user:
            context.user_data.clear()
            keyboard = [
                [InlineKeyboardButton("📝 إنشاء حساب", callback_data="auth_register")],
                [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="auth_login")],
                [InlineKeyboardButton("🔑 نسيت كلمة المرور", callback_data="auth_forgot")],
            ]
            await query.edit_message_text(
                "يجب تسجيل الدخول أولاً. استخدم الأزرار أدناه.",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return
    finally:
        db.close()

    context.user_data["last_menu"] = "main"
    keyboard = [
        [InlineKeyboardButton("📒 دفتر الديون", callback_data="menu_customers")],
        [InlineKeyboardButton("📒 الدخل والمصروف", callback_data="menu_ledger")],
        [InlineKeyboardButton("👤 حسابي", callback_data="menu_profile")],
        [InlineKeyboardButton("🧾 طريقة الاستخدام", callback_data="usage_instructions")],
    ]
    if update.effective_user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("🔐 لوحة الأدمن", callback_data="admin_panel")])
    await query.edit_message_text(
        "القائمة الرئيسية 📒\n\nاختر ما تريد:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def usage_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض شرح طريقة استخدام البوت بشكل مفصل ومنسق."""
    query = update.callback_query
    await query.answer()

    # حماية: لا تعرض الشرح بدون تسجيل دخول.
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == update.effective_user.id).first()
        if not user:
            context.user_data.clear()
            keyboard = [
                [InlineKeyboardButton("📝 إنشاء حساب", callback_data="auth_register")],
                [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="auth_login")],
                [InlineKeyboardButton("🔑 نسيت كلمة المرور", callback_data="auth_forgot")],
            ]
            await query.edit_message_text(
                "يجب تسجيل الدخول أولاً. استخدم الأزرار أدناه.",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return
    finally:
        db.close()

    text = (
        "📘 طريقة الاستخدام (شرح مفصل)\n\n"
        "هذا البوت يساعدك على تتبع ديون العملاء وإدخال دخل/مصروف، مع إمكانية مشاركة التفاصيل.\n\n"
        "1) تسجيل الدخول ✅\n"
        "قبل كل شيء يجب أن يكون لديك حساب داخل النظام.\n"
        "• استخدم زر: «تسجيل الدخول»\n"
        "• ثم أدخل رقم الهاتف وكلمة المرور.\n"
        "• إذا نسيت كلمة المرور استخدم «نسيت كلمة المرور».\n\n"
        "2) دفتر الديون 📒\n"
        "من هنا تدير العملاء والمعاملات (أخذت/أعطيت).\n"
        "• اختر «دفتر الديون» من القائمة.\n"
        "• سترى قائمة العملاء + زر إضافة عميل.\n\n"
        "2.1) إضافة عميل ➕\n"
        "• اضغط «➕ إضافة عميل»\n"
        "• أرسل اسم العميل (إجباري)\n"
        "• ثم أرسل رقم الهاتف (اختياري)\n"
        "• يمكنك تخطي رقم الهاتف إذا لا تريد حفظه.\n\n"
        "2.2) إنشاء معاملة مع العميل 🧾\n"
        "من صفحة العميل اختر واحداً:\n"
        "• 🔴 «أخذت»: يعني العميل دفع لك (يقلّل/يغيّر الرصيد حسب الحالة)\n"
        "• 🟢 «أعطيت»: يعني أنت دفعت/سلمت للعميل (العميل مدين لك)\n\n"
        "ثم سيطلب منك مبلغ المعاملة:\n"
        "• يمكنك كتابة رقم فقط (مثال: 775.25)\n"
        "• أو استخدام الحاسبة التفاعلية 🧮 داخل شاشة إدخال المبلغ\n"
        "  (0-9 + و - و × و ÷ ثم زر = ثم «✅ إدخال المبلغ»).\n\n"
        "بعد المبلغ:\n"
        "• أرسل ملاحظة نصية (اختياري)\n"
        "• أو أرسل صورة مع ملاحظة إن رغبت\n"
        "• ويمكنك تخطي الملاحظة بالزر.\n\n"
        "2.3) تعديل/حذف معاملة ✏️\n"
        "من شاشة «تفاصيل المعاملة» ستجد أزرار:\n"
        "• تعديل المبلغ/الملاحظة/التاريخ/الصورة/النوع\n"
        "• زر «حذف» مع شاشة تأكيد قبل الحذف.\n\n"
        "3) الدخل والمصروف 💵\n"
        "من هنا تدخل قيود مالية (دخل أو مصروف).\n"
        "• اختر «الدخل والمصروف»\n"
        "• اختر صنف الصنف (مثل: راتبك الثابت أو مصروفات...)\n"
        "• أدخل المبلغ رقم فقط\n"
        "• ثم الوصف اختياري\n\n"
        "4) الشراكة/مشاركة الحساب 📤\n"
        "من صفحة العميل يوجد زر «مشاركة».\n"
        "• ستحصل على رابط يمكنك فتحه لمشاهدة المعاملات\n"
        "• ويمكنك إرسال رسالة واتساب مع الرابط.\n\n"
        "5) تذكيرات تسديد الديون 🔔⏰\n"
        "تذكّرك البوت بموعد يتعلق بأحد عملاء الدفتر (مثلاً تاريخ استحقاق أو تسديد مرتقب):\n"
        "• افتح العميل ثم «✏️ تعديل» من أزرار العميل لتظهر خيارات التعديل.\n"
        "• اضغط زر «🔔 تذكيرات التسديد».\n"
        "• اختر تاريخاً ووقتاً لموعد التذكير/الاستحقاق من لوحة التاريخ.\n"
        "• ثم اختر متى تبدأ التذكيرات اليومية (مثلاً قبل 3 أيام، يوم الاستحقاق، …).\n"
        "• من يوم البداية حتى موعد الاستحقاق يرسل البوت تذكيراً يومياً لك (وللطرف المربوط بالعميل إن وُجد).\n"
        "• يمكنك تعديل الموعد لاحقاً من نفس المسار.\n\n"
        "6) كلمة المرور والحساب 🔐\n"
        "• من «👤 حسابي» يمكنك «تغيير الرمز» بإدخال الرمز القديم ثم الرمزين الجديدين للتأكيد.\n"
        "• إن نسيت الرمز الحالي استخدم «نسيت الرمز» من شاشة التغيير أو «نسيت كلمة المرور» من القائمة.\n"
        "• بعد التحقق بالرمز المرسل عبر واتساب يجب عليك تعيين كلمة مرور جديدة (لن يُكتمل الدخول قبل ذلك).\n\n"
        "7) تذكيرات واجهة عامة ⚠️\n"
        "• إذا ظهر زر «◀ رجوع» استخدمه للعودة للخطوة السابقة.\n"
        "• إذا انتهت الجلسة سيظهر زر للقائمة أو تسجيل الدخول.\n"
        "• الحاسبة 🧮 تساعدك في إدخال المبالغ بسرعة.\n\n"
        "إذا تريد، جرّب خطوة كاملة: إضافة عميل ثم معاملة واحدة (أخذت أو أعطيت) مع ملاحظة.\n"
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ رجوع", callback_data="main_menu")]]
        ),
    )
