# -*- coding: utf-8 -*-
"""عمليات تعديل دفتر الديون من الموقع (نفس قواعد البوت)."""
from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote, urlparse

from config import BOT_USERNAME, WEB_BASE_URL, WEB_TX_UPLOAD_DIR
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
from utils.phone import is_plausible_iraq_mobile, normalize_phone, same_phone, wa_number as _wa_number

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


def action_register_web(full_name: str, phone_raw: str, password: str, password2: str) -> str | None:
    """تسجيل مستخدم من الموقع فقط (بدون تيليجرام)."""
    name = (full_name or "").strip()
    if not name:
        return "أدخل الاسم."
    if (password or "").strip() != (password2 or "").strip():
        return "تأكيد كلمة المرور غير مطابق."
    pw = (password or "").strip()
    if len(pw) < 4:
        return "كلمة المرور يجب أن تكون 4 أحرف على الأقل."
    phone = normalize_phone(phone_raw or "")
    if not phone or not is_plausible_iraq_mobile(phone):
        return "رقم الهاتف غير صالح."
    db = SessionLocal()
    try:
        ex = db.query(User).filter(User.phone == phone).first()
        if not ex:
            for u in db.query(User).filter(User.phone.isnot(None)):
                if u.phone and same_phone(u.phone, phone):
                    ex = u
                    break
        if ex:
            return "هذا الرقم مسجل مسبقاً. استخدم تسجيل الدخول."
        u = User(
            telegram_id=None,
            username=None,
            full_name=name,
            phone=phone,
            password_hash=hash_password(pw),
        )
        db.add(u)
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


def _is_public_web_url(url: str) -> bool:
    """HTTP/HTTPS وقابل للاستخدام من خارج السيرفر (نفس منطق البوت)."""
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
    if host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return False
    return True


def build_customer_share_urls(
    user_id: int, cid: int
) -> tuple[str | None, str | None, bool, str | None, str | None]:
    """
    نفس منطق handlers.customers.cust_share: رابط التقرير + واتساب.
    يعيد (view_url, wa_url, using_public_web, share_preview, error).
    """
    db = SessionLocal()
    try:
        cust = _get_customer_owned(db, user_id, cid)
        if not cust:
            return (None, None, False, None, "العميل غير موجود.")
        gave = sum(float(t.amount or 0) for t in cust.transactions if t.kind == "gave")
        took = sum(float(t.amount or 0) for t in cust.transactions if t.kind == "took")
        bal = gave - took
        cur = "د.ع."
        token = secrets.token_urlsafe(16)
        expires = datetime.utcnow() + timedelta(days=30)
        link = ShareLink(customer_id=cust.id, token=token, expires_at=expires)
        db.add(link)
        db.commit()
        base = (WEB_BASE_URL or "").strip().rstrip("/")
        if _is_public_web_url(base):
            view_url = f"{base}/creditbook/balance/{token}?lang=ar"
            using_web = True
        else:
            bot_u = (BOT_USERNAME or "").strip().lstrip("@")
            if not bot_u:
                return (None, None, False, None, "لم يُضبط البوت.")
            view_url = f"https://t.me/{bot_u}?start=view_{token}"
            using_web = False
        if bal > 0:
            msg_balance = f"عليك رصيد {bal:.2f} {cur}"
        elif bal < 0:
            msg_balance = f"لك رصيد {abs(bal):.2f} {cur}"
        else:
            msg_balance = "الرصيد صفر"
        link_hint = "⬇️ المس الرابط لمشاهدة كافة التفاصيل"
        share_text = (
            f"{cust.name}\n\n"
            f"{msg_balance}\n"
            "ــــــــــــــــــــــــ\n"
            f"{link_hint}\n"
            f"{view_url}"
        )
        wa_text = share_text
        wa_num = cust.phone and _wa_number(cust.phone)
        if wa_num:
            wa_url = f"https://api.whatsapp.com/send?phone={wa_num}&text={quote(wa_text)}"
        else:
            wa_url = f"https://api.whatsapp.com/send?text={quote(wa_text)}"
        return (view_url, wa_url, using_web, share_text, None)
    except Exception as e:
        db.rollback()
        return (None, None, False, None, str(e)[:200])
    finally:
        db.close()
