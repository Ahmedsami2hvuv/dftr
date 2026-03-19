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
    reg_name,
    reg_phone,
    login_phone,
    cancel_auth,
    REG_NAME,
    REG_PHONE,
    LOGIN_PHONE,
)
from handlers.ledger import (
    menu_ledger,
    ledger_add_income,
    ledger_add_expense,
    ledger_add_amount,
    ledger_add_desc,
    ledger_skip_desc,
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
    debt_list,
    DEBT_WHO,
    DEBT_AMOUNT,
    DEBT_DESC,
)
from handlers.profile import menu_profile
from handlers.admin import admin_panel, admin_users_list

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))

    # محادثة التسجيل
    reg_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(auth_register, pattern="^auth_register$")],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
            REG_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
    )
    app.add_handler(reg_conv)

    # محادثة تسجيل الدخول
    login_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(auth_login, pattern="^auth_login$")],
        states={
            LOGIN_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
    )
    app.add_handler(login_conv)

    # محادثة إضافة قيد دفتر
    ledger_add_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(ledger_add_income, pattern="^ledger_add_income$"),
            CallbackQueryHandler(ledger_add_expense, pattern="^ledger_add_expense$"),
        ],
        states={
            ADD_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ledger_add_amount)],
            ADD_DESC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ledger_add_desc),
                CommandHandler("skip", ledger_skip_desc),
                CommandHandler("تخطى", ledger_skip_desc),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
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
                CommandHandler("skip", debt_desc),
                CommandHandler("تخطى", debt_desc),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_auth)],
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

    logger.info("البوت يعمل...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
