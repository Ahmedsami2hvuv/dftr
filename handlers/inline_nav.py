# -*- coding: utf-8 -*-
"""أزرار رجوع/إلغاء مشتركة لرسائل البوت."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="main_menu")]])


def kb_menu_customers() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀ دفتر الديون", callback_data="menu_customers")]])


def kb_menu_ledger() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀ الدخل والمصروف", callback_data="menu_ledger")]])


def kb_menu_debts() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀ الديون", callback_data="menu_debts")]])


def kb_admin_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀ لوحة الأدمن", callback_data="admin_panel")]])


def kb_tx_detail(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("◀ رجوع للمعاملة", callback_data=f"cust_tx_{tx_id}")]]
    )
