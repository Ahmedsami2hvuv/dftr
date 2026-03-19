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
    auth_logout,
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
    ledger_pick_category_click,
    ledger_add_amount,
    ledger_add_desc,
    ledger_skip_desc_click,
    ledger_list,
    ledger_categories_menu,
    ledger_cat_add_start,
    ledger_cat_name_done,
    ledger_cat_kind_took_click,
    ledger_cat_kind_gave_click,
    ledger_cat_del_req_click,
    ledger_cat_del_do_click,
    ADD_AMOUNT,
    ADD_DESC,
    CAT_ADD_NAME,
    CAT_ADD_KIND,
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
from handlers.admin import (
    admin_panel,
    admin_users_list,
    admin_user_detail,
    admin_feedbacks_list,
    admin_feedback_detail,
    admin_feedback_toggle_status,
    admin_feedback_search_start,
    admin_feedback_search_do,
    admin_feedback_search_cancel,
    admin_broadcast_start,
    admin_broadcast_receive_content,
    admin_broadcast_toggle_buttons,
    admin_broadcast_cancel,
    admin_broadcast_send,
    admin_brand_logo_start,
    admin_brand_logo_on_message,
    admin_brand_logo_cancel,
    admin_brand_logo_cancel_cmd,
    bc_start_click,
    bc_update_click,
    ADMIN_BROADCAST_CONTENT,
    ADMIN_BROADCAST_BUTTONS,
    ADMIN_FEEDBACK_SEARCH,
    ADMIN_BRAND_LOGO,
)
from handlers.feedback import (
    feedback_from_profile,
    feedback_from_broadcast_comment,
    feedback_from_broadcast_suggest,
    feedback_receive,
    feedback_cancel_click,
    FEEDBACK_WAIT,
)
from web_server import start_web_server
from handlers.customers import (
    menu_customers,
    cust_add_start,
    cust_name,
    cust_phone,
    cust_phone_skip_click,
    cust_search_start,
    cust_search_query_done,
    cust_search_cancel_click,
    cust_took,
    cust_gave,
    cust_amount,
    cust_amount_photo,
    cust_note,
    cust_note_photo,
    cust_note_skip_click,
    cust_txn_back_click,
    cust_txn_back_amount_click,
    cust_txn_cancel_click,
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
    menu_customer_categories,
    cust_cat_add_start,
    cust_cat_name_done,
    cust_cat_kind_took_click,
    cust_cat_kind_gave_click,
    cust_cat_del_req_click,
    cust_cat_del_do_click,
    CAT_ADD_NAME,
    CAT_ADD_KIND,
    TX_EDIT_AMOUNT,
    TX_EDIT_NOTE,
    TX_EDIT_DATE,
    TX_EDIT_PHOTO,
    CUST_NAME,
    CUST_PHONE,
    CUST_SEARCH_QUERY,
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

    # أولوية أعلى لـ /start حتى لا تلتقطه محادثة أخرى وتُظهر قائمة قديمة
    app.add_handler(CommandHandler("start", cmd_start), group=-1)

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
            CallbackQueryHandler(ledger_pick_category_click, pattern="^ledger_pick_cat_\\d+$"),
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

    # محادثة إضافة صنف في الدخل والمصروف (أصناف الصنف)
    ledger_cat_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ledger_cat_add_start, pattern="^ledger_cat_add$")],
        states={
            CAT_ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ledger_cat_name_done)],
            CAT_ADD_KIND: [
                CallbackQueryHandler(ledger_cat_kind_took_click, pattern="^ledger_cat_kind_took$"),
                CallbackQueryHandler(ledger_cat_kind_gave_click, pattern="^ledger_cat_kind_gave$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
        per_message=False,
    )
    app.add_handler(ledger_cat_add_conv)

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

    # محادثة بث الأدمن
    admin_broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast$")],
        states={
            ADMIN_BROADCAST_CONTENT: [
                MessageHandler(~filters.COMMAND, admin_broadcast_receive_content),
                CallbackQueryHandler(admin_broadcast_cancel, pattern="^admin_broadcast_cancel$"),
            ],
            ADMIN_BROADCAST_BUTTONS: [
                CallbackQueryHandler(admin_broadcast_toggle_buttons, pattern="^admin_bc_toggle_"),
                CallbackQueryHandler(admin_broadcast_send, pattern="^admin_bc_send$"),
                CallbackQueryHandler(admin_broadcast_cancel, pattern="^admin_broadcast_cancel$"),
            ],
        },
        fallbacks=[],
        allow_reentry=True,
        per_chat=True,
        per_message=False,
    )
    app.add_handler(admin_broadcast_conv)

    admin_brand_logo_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_brand_logo_start, pattern="^admin_brand_logo$")],
        states={
            ADMIN_BRAND_LOGO: [
                CallbackQueryHandler(admin_brand_logo_cancel, pattern="^admin_brand_logo_cancel$"),
                MessageHandler(~filters.COMMAND, admin_brand_logo_on_message),
            ],
        },
        fallbacks=[CommandHandler("cancel", admin_brand_logo_cancel_cmd)],
        allow_reentry=True,
        per_chat=True,
        per_message=False,
    )
    # أولوية عالية حتى لا تخطف محادثات أخرى (مثل دفتر العملاء) صورة الشعار
    app.add_handler(admin_brand_logo_conv, group=-1)

    # محادثة بحث صندوق المشاكل/الاقتراحات (أدمن)
    admin_feedback_search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_feedback_search_start, pattern="^admin_feedback_search$")],
        states={
            ADMIN_FEEDBACK_SEARCH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_feedback_search_do),
                CallbackQueryHandler(admin_feedback_search_cancel, pattern="^admin_feedback_search_cancel$"),
            ]
        },
        fallbacks=[],
        allow_reentry=True,
        per_chat=True,
        per_message=False,
    )
    app.add_handler(admin_feedback_search_conv)

    # محادثة إرسال مشكلة/اقتراح للمستخدمين
    feedback_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(feedback_from_profile, pattern="^send_feedback$"),
            CallbackQueryHandler(feedback_from_broadcast_comment, pattern="^bc_comment$"),
            CallbackQueryHandler(feedback_from_broadcast_suggest, pattern="^bc_suggest$"),
        ],
        states={
            FEEDBACK_WAIT: [
                MessageHandler(~filters.COMMAND, feedback_receive),
                CallbackQueryHandler(feedback_cancel_click, pattern="^feedback_cancel$"),
            ]
        },
        fallbacks=[],
        allow_reentry=True,
        per_chat=True,
        per_message=False,
    )
    app.add_handler(feedback_conv)

    # أزرار القوائم
    app.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(menu_ledger, pattern="^menu_ledger$"))
    app.add_handler(CallbackQueryHandler(ledger_list, pattern="^ledger_list$"))
    app.add_handler(CallbackQueryHandler(ledger_categories_menu, pattern="^ledger_categories_menu$"))
    app.add_handler(
        CallbackQueryHandler(ledger_cat_del_req_click, pattern="^ledger_cat_del_req_\\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(ledger_cat_del_do_click, pattern="^ledger_cat_del_do_\\d+$")
    )
    app.add_handler(CallbackQueryHandler(menu_debts, pattern="^menu_debts$"))
    app.add_handler(CallbackQueryHandler(debt_list, pattern="^debt_list$"))
    app.add_handler(CallbackQueryHandler(menu_profile, pattern="^menu_profile$"))
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(admin_users_list, pattern="^admin_users$"))
    app.add_handler(CallbackQueryHandler(admin_user_detail, pattern="^admin_user_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_feedbacks_list, pattern="^admin_feedbacks$"))
    app.add_handler(CallbackQueryHandler(admin_feedbacks_list, pattern="^admin_feedbacks_open$"))
    app.add_handler(CallbackQueryHandler(admin_feedbacks_list, pattern="^admin_feedbacks_done$"))
    app.add_handler(CallbackQueryHandler(admin_feedback_detail, pattern="^admin_feedback_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_feedback_toggle_status, pattern="^admin_feedback_toggle_\\d+$"))
    app.add_handler(CallbackQueryHandler(bc_start_click, pattern="^bc_start$"))
    app.add_handler(CallbackQueryHandler(bc_update_click, pattern="^bc_update$"))

    # دفتر الديون (عملاء)
    app.add_handler(CallbackQueryHandler(menu_customers, pattern="^menu_customers$"))

    # تسجيل خروج
    app.add_handler(CallbackQueryHandler(auth_logout, pattern="^auth_logout$"))

    cust_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cust_add_start, pattern="^cust_add$")],
        states={
            CUST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_name)],
            CUST_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cust_phone),
                MessageHandler(filters.CONTACT, cust_phone),
                CallbackQueryHandler(cust_phone_skip_click, pattern="^cust_phone_skip_btn$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
        per_message=False,
    )
    app.add_handler(cust_add_conv)

    # بحث العملاء داخل دفتر الديون
    cust_search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cust_search_start, pattern="^cust_search_start$")],
        states={
            CUST_SEARCH_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cust_search_query_done),
                CallbackQueryHandler(cust_search_cancel_click, pattern="^cust_search_cancel$"),
            ],
        },
        fallbacks=[],
        allow_reentry=True,
        per_chat=True,
        per_message=False,
    )
    app.add_handler(cust_search_conv)

    cust_txn_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cust_took, pattern="^cust_took_\\d+$"),
            CallbackQueryHandler(cust_gave, pattern="^cust_gave_\\d+$"),
        ],
        states={
            CUST_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cust_amount),
                MessageHandler(filters.PHOTO, cust_amount_photo),
                CallbackQueryHandler(cust_txn_back_click, pattern="^cust_txn_back_\\d+$"),
                CallbackQueryHandler(cust_txn_cancel_click, pattern="^cust_txn_cancel$"),
                CallbackQueryHandler(cust_callback_router, pattern="^cust_"),
            ],
            CUST_NOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cust_note),
                MessageHandler(filters.PHOTO, cust_note_photo),
                CallbackQueryHandler(cust_note_skip_click, pattern="^cust_note_skip_btn$"),
                CallbackQueryHandler(cust_txn_back_amount_click, pattern="^cust_txn_back_amount$"),
                CallbackQueryHandler(cust_txn_back_click, pattern="^cust_txn_back_\\d+$"),
                CallbackQueryHandler(cust_txn_cancel_click, pattern="^cust_txn_cancel$"),
                CallbackQueryHandler(cust_callback_router, pattern="^cust_"),
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
            CUST_EDIT_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cust_edit_phone_done),
                MessageHandler(filters.CONTACT, cust_edit_phone_done),
            ],
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

    # محادثة إضافة صنف (أصناف الصنف)
    cust_cat_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cust_cat_add_start, pattern="^cust_cat_add$")],
        states={
            CAT_ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_cat_name_done)],
            CAT_ADD_KIND: [
                CallbackQueryHandler(cust_cat_kind_took_click, pattern="^cust_cat_kind_took$"),
                CallbackQueryHandler(cust_cat_kind_gave_click, pattern="^cust_cat_kind_gave$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
        per_message=False,
    )
    app.add_handler(cust_cat_conv)

    # router لكل callbacks الخاصة بالعميل (تفاصيل/حذف/مشاركة/قائمة)
    app.add_handler(CallbackQueryHandler(cust_callback_router, pattern="^cust_"))

    logger.info("البوت يعمل...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
