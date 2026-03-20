# -*- coding: utf-8 -*-
"""عمليات تعديل دفتر الديون من الموقع (نفس قواعد البوت)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from database import SessionLocal
from app_models import (
    Customer,
    CustomerTransaction,
    CustomerPaymentReminder,
    PartnerLink,
    PartnerPendingTx,
    ShareLink,
)
from handlers.customers import _parse_amount_and_optional_note
from handlers.partner_link import maybe_queue_partner_tx
from utils.phone import is_plausible_iraq_mobile, normalize_phone


def _get_customer_owned(db, user_id: int, cid: int) -> Customer | None:
    return (
        db.query(Customer)
        .filter(Customer.id == cid, Customer.user_id == user_id)
        .first()
    )


def _get_tx_owned(db, user_id: int, tx_id: int) -> tuple[CustomerTransaction, Customer] | None:
    tx = db.query(CustomerTransaction).filter(CustomerTransaction.id == tx_id).first()
    if not tx:
        return None
    cust = db.query(Customer).filter(Customer.id == tx.customer_id).first()
    if not cust or cust.user_id != user_id:
        return None
    return tx, cust


def parse_amount_simple(text: str) -> Decimal | None:
    s = (text or "").replace(",", "").strip()
    if not s:
        return None
    try:
        d = Decimal(s)
        if d <= 0:
            return None
        return d
    except (InvalidOperation, ValueError):
        return None


def action_customer_create(user_id: int, name: str, phone_raw: str) -> tuple[str | None, int | None]:
    name = (name or "").strip()
    if not name:
        return ("أدخل اسم العميل.", None)
    phone_raw = (phone_raw or "").strip()
    phone_norm = normalize_phone(phone_raw) if phone_raw else None
    if phone_norm and not is_plausible_iraq_mobile(phone_norm):
        return ("رقم الهاتف غير صالح.", None)
    db = SessionLocal()
    try:
        c = Customer(user_id=user_id, name=name, phone=phone_norm)
        db.add(c)
        db.commit()
        db.refresh(c)
        return (None, c.id)
    except Exception as e:
        db.rollback()
        return (str(e)[:200], None)
    finally:
        db.close()


def action_customer_update(user_id: int, cid: int, name: str, phone_raw: str) -> str | None:
    name = (name or "").strip()
    if not name:
        return "أدخل اسماً صحيحاً."
    db = SessionLocal()
    try:
        cust = _get_customer_owned(db, user_id, cid)
        if not cust:
            return "العميل غير موجود."
        phone_raw = (phone_raw or "").strip()
        if phone_raw.lower() in ("", "حذف", "delete"):
            cust.phone = None
        else:
            p = normalize_phone(phone_raw)
            if not is_plausible_iraq_mobile(p):
                return "رقم الهاتف غير صالح أو اكتب: حذف لإزالة الرقم."
            cust.phone = p
        cust.name = name
        db.commit()
        return None
    except Exception as e:
        db.rollback()
        return str(e)[:200]
    finally:
        db.close()


def action_customer_delete(user_id: int, cid: int) -> str | None:
    """نفس تسلسل حذف البوت: روابط، تذكيرات، معاملات، مشاركة."""
    db = SessionLocal()
    try:
        cust = _get_customer_owned(db, user_id, cid)
        if not cust:
            return "العميل غير موجود."
        tx_ids = [
            r[0]
            for r in db.query(CustomerTransaction.id).filter(CustomerTransaction.customer_id == cid).all()
        ]
        links = (
            db.query(PartnerLink)
            .filter((PartnerLink.inviter_customer_id == cid) | (PartnerLink.invitee_customer_id == cid))
            .all()
        )
        link_ids = [l.id for l in links]

        if link_ids:
            db.query(PartnerPendingTx).filter(PartnerPendingTx.partner_link_id.in_(link_ids)).delete(
                synchronize_session=False
            )

        if tx_ids:
            db.query(PartnerPendingTx).filter(PartnerPendingTx.source_tx_id.in_(tx_ids)).delete(
                synchronize_session=False
            )
            db.query(PartnerPendingTx).filter(PartnerPendingTx.mirrored_tx_id.in_(tx_ids)).delete(
                synchronize_session=False
            )

        db.query(CustomerPaymentReminder).filter(CustomerPaymentReminder.customer_id == cid).delete(
            synchronize_session=False
        )

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
        return None
    except Exception as e:
        db.rollback()
        return str(e)[:200]
    finally:
        db.close()


def action_txn_add(user_id: int, cid: int, kind: str, amount_text: str, note_text: str) -> str | None:
    if kind not in ("gave", "took"):
        return "نوع المعاملة غير صالح."
    amt_line = (amount_text or "").strip()
    note_line = (note_text or "").strip()
    if amt_line and note_line:
        combined = amt_line + "\n" + note_line
    elif note_line:
        combined = note_line
    else:
        combined = amt_line
    amt, note_opt = _parse_amount_and_optional_note(combined)
    if amt is None:
        return "لم أستخرج مبلغاً صحيحاً (مثال: 775.25 أو سطران: المبلغ ثم الملاحظة)."
    db = SessionLocal()
    try:
        cust = _get_customer_owned(db, user_id, cid)
        if not cust:
            return "العميل غير موجود."
        t = CustomerTransaction(
            customer_id=cid,
            amount=amt,
            kind=kind,
            note=note_opt,
            photo_file_id=None,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        maybe_queue_partner_tx(db, t)
        return None
    except Exception as e:
        db.rollback()
        return str(e)[:200]
    finally:
        db.close()


def action_tx_update(user_id: int, tx_id: int, amount_text: str, note_text: str) -> str | None:
    amt = parse_amount_simple(amount_text)
    if amt is None:
        return "أدخل مبلغاً أكبر من صفر."
    note = (note_text or "").strip()
    note_val = None if note.lower() in ("حذف", "delete") else (note or None)
    db = SessionLocal()
    try:
        got = _get_tx_owned(db, user_id, tx_id)
        if not got:
            return "المعاملة غير موجودة."
        tx, _cust = got
        tx.amount = amt
        tx.note = note_val
        db.commit()
        return None
    except Exception as e:
        db.rollback()
        return str(e)[:200]
    finally:
        db.close()


def action_tx_toggle_kind(user_id: int, tx_id: int) -> str | None:
    db = SessionLocal()
    try:
        got = _get_tx_owned(db, user_id, tx_id)
        if not got:
            return "المعاملة غير موجودة."
        tx, _cust = got
        tx.kind = "gave" if tx.kind == "took" else "took"
        db.commit()
        return None
    except Exception as e:
        db.rollback()
        return str(e)[:200]
    finally:
        db.close()


def action_tx_delete(user_id: int, tx_id: int) -> tuple[str | None, int | None]:
    """إرجاع (خطأ، None) أو (None، customer_id) بعد الحذف."""
    db = SessionLocal()
    try:
        got = _get_tx_owned(db, user_id, tx_id)
        if not got:
            return ("المعاملة غير موجودة.", None)
        tx, _cust = got
        cid = tx.customer_id
        db.delete(tx)
        db.commit()
        return (None, cid)
    except Exception as e:
        db.rollback()
        return (str(e)[:200], None)
    finally:
        db.close()
