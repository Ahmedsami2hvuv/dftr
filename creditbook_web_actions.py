# -*- coding: utf-8 -*-
"""عمليات تعديل دفتر الديون من الموقع (نفس قواعد البوت)."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from config import WEB_TX_UPLOAD_DIR
from database import SessionLocal
from app_models import (
    Customer,
    CustomerTransaction,
    CustomerPaymentReminder,
    PartnerLink,
    PartnerPendingTx,
    ShareLink,
    User,
)
from handlers.customers import _parse_amount_and_optional_note
from handlers.partner_link import maybe_queue_partner_tx
from utils.password import check_password, hash_password
from utils.phone import is_plausible_iraq_mobile, normalize_phone

_WEB_PHOTO_SAFE = re.compile(r"^[a-f0-9]{32}\.(jpg|jpeg|png|gif|webp)$", re.I)


def is_safe_web_photo_name(name: str) -> bool:
    return bool(name and _WEB_PHOTO_SAFE.match(name))


def unlink_web_photo(photo_file_id: str | None) -> None:
    if not photo_file_id or not str(photo_file_id).startswith("web:"):
        return
    name = str(photo_file_id)[4:]
    if not is_safe_web_photo_name(name):
        return
    p = WEB_TX_UPLOAD_DIR / name
    try:
        if p.is_file():
            p.unlink()
    except OSError:
        pass


def save_web_photo_bytes(data: bytes, orig_filename: str) -> tuple[str | None, str | None]:
    """إرجاع (اسم الملف المحفوظ، رسالة خطأ)."""
    if not data or len(data) > 5_000_000:
        return None, "حجم الصورة يجب أن يكون أقل من 5 ميجابايت."
    ext = Path(orig_filename or "").suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        ext = ".jpg"
    name = f"{uuid.uuid4().hex}{ext}"
    WEB_TX_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (WEB_TX_UPLOAD_DIR / name).write_bytes(data)
    return name, None


def parse_tx_datetime(s: str | None) -> datetime | None:
    if not s or not str(s).strip():
        return None
    s = str(s).strip()
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


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
                return "رقم الهاتف غير صالح، أو اتركه فارغاً لإزالة الرقم."
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
        for (pf,) in db.query(CustomerTransaction.photo_file_id).filter(CustomerTransaction.customer_id == cid).all():
            unlink_web_photo(pf)
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


def action_txn_add(
    user_id: int,
    cid: int,
    kind: str,
    amount_text: str,
    note_text: str,
    created_at: datetime | None = None,
    photo_bytes: bytes | None = None,
    photo_oriname: str | None = None,
) -> str | None:
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
    photo_id = None
    if photo_bytes and len(photo_bytes) > 0:
        fn, err = save_web_photo_bytes(photo_bytes, photo_oriname or "")
        if err:
            return err
        if fn:
            photo_id = f"web:{fn}"
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
            photo_file_id=photo_id,
        )
        if created_at is not None:
            t.created_at = created_at
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


def action_tx_update(
    user_id: int,
    tx_id: int,
    amount_text: str,
    note_text: str,
    created_at: datetime | None = None,
    photo_bytes: bytes | None = None,
    photo_oriname: str | None = None,
    remove_photo: bool = False,
) -> str | None:
    amt = parse_amount_simple(amount_text)
    if amt is None:
        return "أدخل مبلغاً أكبر من صفر."
    note = (note_text or "").strip()
    note_val = note if note else None
    db = SessionLocal()
    try:
        got = _get_tx_owned(db, user_id, tx_id)
        if not got:
            return "المعاملة غير موجودة."
        tx, _cust = got
        tx.amount = amt
        tx.note = note_val
        if created_at is not None:
            tx.created_at = created_at
        if remove_photo:
            unlink_web_photo(tx.photo_file_id)
            tx.photo_file_id = None
        elif photo_bytes and len(photo_bytes) > 0:
            unlink_web_photo(tx.photo_file_id)
            fn, err = save_web_photo_bytes(photo_bytes, photo_oriname or "")
            if err:
                return err
            tx.photo_file_id = f"web:{fn}" if fn else None
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
        unlink_web_photo(tx.photo_file_id)
        db.delete(tx)
        db.commit()
        return (None, cid)
    except Exception as e:
        db.rollback()
        return (str(e)[:200], None)
    finally:
        db.close()


def action_user_update_profile(user_id: int, full_name: str, phone_raw: str) -> str | None:
    name = (full_name or "").strip()
    if not name:
        return "أدخل اسماً صحيحاً."
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user_id).first()
        if not u:
            return "الحساب غير موجود."
        phone_raw = (phone_raw or "").strip()
        if not phone_raw:
            u.phone = None
        else:
            p = normalize_phone(phone_raw)
            if not is_plausible_iraq_mobile(p):
                return "رقم الهاتف غير صالح."
            u.phone = p
        u.full_name = name
        db.commit()
        return None
    except Exception as e:
        db.rollback()
        return str(e)[:200]
    finally:
        db.close()


def action_user_change_password(user_id: int, current: str, new_pw: str, new_pw2: str) -> str | None:
    if (new_pw or "").strip() != (new_pw2 or "").strip():
        return "تأكيد كلمة المرور غير مطابق."
    if len((new_pw or "").strip()) < 4:
        return "كلمة المرور الجديدة قصيرة جداً."
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user_id).first()
        if not u:
            return "الحساب غير موجود."
        if not u.password_hash:
            return "لا توجد كلمة مرور لهذا الحساب بعد."
        if not check_password((current or "").strip(), u.password_hash):
            return "كلمة المرور الحالية غير صحيحة."
        u.password_hash = hash_password((new_pw or "").strip())
        db.commit()
        return None
    except Exception as e:
        db.rollback()
        return str(e)[:200]
    finally:
        db.close()
