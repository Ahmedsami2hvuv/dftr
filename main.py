# -*- coding: utf-8 -*-
"""
بوت تليجرام - دفتر حسابات (مثل كناش)
التوكن والبيانات في متغيرات البيئة (Railway)
"""
import sys
from pathlib import Path

# تأكد أن جذر المشروع في مسار الاستيراد (مهم على Railway)
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import logging
import threading
from dotenv import load_dotenv
load_dotenv()

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)

from config import BOT_TOKEN
from database import init_db

from handlers.start import cmd_start, main_menu
from handlers.auth import (
    auth_register,
    auth_login,
    auth_forgot,
    reg_name,
    reg_phone,
    reg_password,
    login_phone,
    login_password,
    forgot_phone,
    forgot_enter_code_click,
    forgot_code,
    forgot_back_phone_click,
    forgot_copy_code_click,
    cancel_auth,
    REG_NAME,
    REG_PHONE,
    REG_PASSWORD,
    LOGIN_PHONE,
    LOGIN_PASSWORD,
    FORGOT_PHONE,
    FORGOT_WAIT,
    FORGOT_CODE,
)
from handlers.ledger_handler import (
    menu_ledger,
    ledger_add_fixed_salary,
    ledger_add_additional_income,
    ledger_add_expenses,
    ledger_add_amount,
    ledger_add_desc,
    ledger_skip_desc_click,
    ledger_list,
    ADD_AMOUNT,
    ADD_DESC,
)
from handlers.debts import (
    menu_debts,
    debt_add_they_owe,
    debt_add_i_owe,
    debt_who,
    debt_amount,
    debt_desc,
    debt_skip_desc_click,
    debt_list,
    DEBT_WHO,
    DEBT_AMOUNT,
    DEBT_DESC,
)
from handlers.profile import menu_profile
from handlers.admin import admin_panel, admin_users_list
from web_server import start_web_server
from handlers.customers import (
    menu_customers,
    cust_add_start,
    cust_name,
    cust_phone,
    cust_phone_skip_click,
    cust_took,
    cust_gave,
    cust_amount,
    cust_note,
    cust_note_skip_click,
    cust_edit_name_start,
    cust_edit_phone_start,
    cust_edit_name_done,
    cust_edit_phone_done,
    cust_callback_router,
    cust_tx_detail,
    cust_tx_delete_click,
    cust_tx_toggle_kind_click,
    cust_tx_edit_amount_start,
    cust_tx_edit_amount_done,
    cust_tx_edit_note_start,
    cust_tx_edit_note_done,
    cust_tx_edit_date_start,
    cust_tx_edit_date_done,
    cust_tx_edit_photo_start,
    cust_tx_edit_photo_done,
    cust_tx_edit_photo_back_click,
    TX_EDIT_AMOUNT,
    TX_EDIT_NOTE,
    TX_EDIT_DATE,
    TX_EDIT_PHOTO,
    CUST_NAME,
    CUST_PHONE,
    CUST_AMOUNT,
    CUST_NOTE,
    CUST_EDIT_NAME,
    CUST_EDIT_PHONE,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # تشغيل الموقع البسيط الذي يعرض معاملات العميل من خلال رابط المشاركة
    try:
        from config import WEB_PORT

        httpd = start_web_server(WEB_PORT)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        logger.info("الموقع يعمل على المنفذ: %s", WEB_PORT)
    except Exception as e:
        logger.warning("تعذر تشغيل الموقع: %s", e)

    app.add_handler(CommandHandler("start", cmd_start))

    # محادثة التسجيل — per_message=False حتى يُقبل رسالة الاسم/الرقم بعد الضغط على الزر
    reg_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(auth_register, pattern="^auth_register$")],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
            REG_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_phone),
                MessageHandler(filters.CONTACT, reg_phone),
            ],
            REG_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
        per_message=False,
    )
    app.add_handler(reg_conv)

    # محادثة تسجيل الدخول
    login_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(auth_login, pattern="^auth_login$")],
        states={
            LOGIN_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, login_phone),
                MessageHandler(filters.CONTACT, login_phone),
            ],
            LOGIN_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
        per_message=False,
    )
    app.add_handler(login_conv)

    # محادثة نسيت كلمة المرور: رقم الهاتف -> زر استلام الرمز -> إدخال الرمز
    forgot_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(auth_forgot, pattern="^auth_forgot$")],
        states={
            FORGOT_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, forgot_phone),
                MessageHandler(filters.CONTACT, forgot_phone),
            ],
            FORGOT_WAIT: [CallbackQueryHandler(forgot_enter_code_click, pattern="^forgot_enter_code$")],
            FORGOT_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, forgot_code),
                CallbackQueryHandler(forgot_back_phone_click, pattern="^forgot_back_phone$"),
                CallbackQueryHandler(forgot_copy_code_click, pattern="^forgot_copy_code$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
        per_message=False,
    )
    app.add_handler(forgot_conv)

    # محادثة إضافة قيد دفتر
    ledger_add_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(ledger_add_fixed_salary, pattern="^ledger_add_fixed_salary$"),
            CallbackQueryHandler(ledger_add_additional_income, pattern="^ledger_add_additional_income$"),
            CallbackQueryHandler(ledger_add_expenses, pattern="^ledger_add_expenses$"),
        ],
        states={
            ADD_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ledger_add_amount)],
            ADD_DESC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ledger_add_desc),
                CallbackQueryHandler(ledger_skip_desc_click, pattern="^ledger_skip_desc_btn$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
        per_message=True,
    )
    app.add_handler(ledger_add_conv)

    # محادثة إضافة دين
    debt_add_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(debt_add_they_owe, pattern="^debt_add_they_owe$"),
            CallbackQueryHandler(debt_add_i_owe, pattern="^debt_add_i_owe$"),
        ],
        states={
            DEBT_WHO: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_who)],
            DEBT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_amount)],
            DEBT_DESC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, debt_desc),
                CallbackQueryHandler(debt_skip_desc_click, pattern="^debt_skip_desc_btn$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
        per_message=True,
    )
    app.add_handler(debt_add_conv)

    # أزرار القوائم
    app.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(menu_ledger, pattern="^menu_ledger$"))
    app.add_handler(CallbackQueryHandler(ledger_list, pattern="^ledger_list$"))
    app.add_handler(CallbackQueryHandler(menu_debts, pattern="^menu_debts$"))
    app.add_handler(CallbackQueryHandler(debt_list, pattern="^debt_list$"))
    app.add_handler(CallbackQueryHandler(menu_profile, pattern="^menu_profile$"))
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(admin_users_list, pattern="^admin_users$"))

    # دفتر الديون (عملاء)
    app.add_handler(CallbackQueryHandler(menu_customers, pattern="^menu_customers$"))

    cust_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cust_add_start, pattern="^cust_add$")],
        states={
            CUST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_name)],
            CUST_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cust_phone),
                CallbackQueryHandler(cust_phone_skip_click, pattern="^cust_phone_skip_btn$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
        per_message=False,
    )
    app.add_handler(cust_add_conv)

    cust_txn_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cust_took, pattern="^cust_took_\\d+$"),
            CallbackQueryHandler(cust_gave, pattern="^cust_gave_\\d+$"),
        ],
        states={
            CUST_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_amount)],
            CUST_NOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cust_note),
                CallbackQueryHandler(cust_note_skip_click, pattern="^cust_note_skip_btn$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
        per_message=False,
    )
    app.add_handler(cust_txn_conv)

    cust_edit_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cust_edit_name_start, pattern="^cust_editname_\\d+$"),
            CallbackQueryHandler(cust_edit_phone_start, pattern="^cust_editphone_\\d+$"),
        ],
        states={
            CUST_EDIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_edit_name_done)],
            CUST_EDIT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_edit_phone_done)],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
        per_message=False,
    )
    app.add_handler(cust_edit_conv)

    # محادثة تعديل معاملة العميل (مبلغ/ملاحظة/تاريخ/صورة)
    cust_tx_edit_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cust_tx_edit_amount_start, pattern="^cust_tx_edit_amount_\\d+$"),
            CallbackQueryHandler(cust_tx_edit_note_start, pattern="^cust_tx_edit_note_\\d+$"),
            CallbackQueryHandler(cust_tx_edit_date_start, pattern="^cust_tx_edit_date_\\d+$"),
            CallbackQueryHandler(cust_tx_edit_photo_start, pattern="^cust_tx_edit_photo_\\d+$"),
        ],
        states={
            TX_EDIT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_tx_edit_amount_done)],
            TX_EDIT_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_tx_edit_note_done)],
            TX_EDIT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_tx_edit_date_done)],
            TX_EDIT_PHOTO: [
                MessageHandler(filters.PHOTO, cust_tx_edit_photo_done),
                CallbackQueryHandler(cust_tx_edit_photo_back_click, pattern="^cust_tx_edit_photo_back_\\d+$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
        per_message=False,
    )
    app.add_handler(cust_tx_edit_conv)

    # router لكل callbacks الخاصة بالعميل (تفاصيل/حذف/مشاركة/قائمة)
    app.add_handler(CallbackQueryHandler(cust_callback_router, pattern="^cust_"))

    logger.info("البوت يعمل...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
