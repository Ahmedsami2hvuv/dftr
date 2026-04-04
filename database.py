# -*- coding: utf-8 -*-
"""اتصال قاعدة البيانات PostgreSQL (Railway)"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from config import DATABASE_URL

if not DATABASE_URL:
    raise ValueError("DATABASE_URL مطلوب. أضف قاعدة PostgreSQL في Railway ثم DATABASE_URL")

# Railway قد يعطي رابط بصيغة postgres:// نحوّله إلى postgresql://
url = DATABASE_URL
if url.startswith("postgres://"):
    url = "postgresql://" + url[10:]

engine = create_engine(url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """إنشاء الجداول عند أول تشغيل + إضافة أعمدة للمستخدمين القدامى"""
    from app_models import (
        User,
        LedgerEntry,
        Debt,
        Customer,
        CustomerTransaction,
        TransactionHistory,
        ShareLink,
        CustomerCategory,
        LedgerCategory,
        FeedbackMessage,
        SiteSetting,
        PartnerLink,
        PartnerPendingTx,
        CustomerPaymentReminder,
    )  # noqa: F401
    Base.metadata.create_all(bind=engine)
    # إضافة أعمدة جديدة لجدول users إن وُجد بدونها (ترحيل بسيط)
    with engine.connect() as conn:
        for col, typ in [
            ("password_hash", "VARCHAR(128)"),
            ("reset_code", "VARCHAR(10)"),
            ("reset_code_expires", "TIMESTAMP"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {typ}"))
                conn.commit()
            except Exception:
                pass

        # إزالة قيد NOT NULL من تيليجرام_id للسماح بالتسجيل من خلال الموقع فقط
        try:
            conn.execute(
                text("ALTER TABLE users ALTER COLUMN telegram_id DROP NOT NULL")
            )
            conn.commit()
        except Exception:
            pass

        # عمود صورة للمعاملات
        try:
            conn.execute(
                text("ALTER TABLE customer_transactions ADD COLUMN IF NOT EXISTS photo_file_id VARCHAR(255)")
            )
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(
                text("ALTER TABLE customer_transactions ADD COLUMN IF NOT EXISTS photo_web_blob BYTEA")
            )
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(
                text("ALTER TABLE transaction_history ADD COLUMN IF NOT EXISTS photo_web_blob BYTEA")
            )
            conn.commit()
        except Exception:
            pass

        # عمود التصنيف لدفتر الحسابات
        try:
            conn.execute(
                text("ALTER TABLE ledger_entries ADD COLUMN IF NOT EXISTS category VARCHAR(50)")
            )
            conn.commit()
        except Exception:
            pass

        # حالة معالجة رسالة المشكلة/الاقتراح
        try:
            conn.execute(
                text("ALTER TABLE feedback_messages ADD COLUMN IF NOT EXISTS is_resolved INTEGER DEFAULT 0")
            )
            conn.commit()
        except Exception:
            pass

        # وقت استحقاق كامل لتذكيرات التسديد
        try:
            conn.execute(
                text(
                    "ALTER TABLE customer_payment_reminders ADD COLUMN IF NOT EXISTS due_at TIMESTAMP"
                )
            )
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(
                text(
                    "UPDATE customer_payment_reminders SET due_at = due_date::timestamp "
                    "WHERE due_at IS NULL AND due_date IS NOT NULL"
                )
            )
            conn.commit()
        except Exception:
            pass

        # آخر نشاط للعميل — ترتيب القائمة في الموقع والبوت
        try:
            conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("UPDATE customers SET updated_at = created_at WHERE updated_at IS NULL"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(
                text(
                    "UPDATE customers AS c SET updated_at = s.mx FROM ("
                    "SELECT customer_id, MAX(created_at) AS mx FROM customer_transactions GROUP BY customer_id"
                    ") AS s WHERE c.id = s.customer_id AND s.mx > COALESCE(c.updated_at, c.created_at)"
                )
            )
            conn.commit()
        except Exception:
            pass

    _register_customer_activity_listeners_once()


_customer_activity_listeners_registered = False


def _register_customer_activity_listeners_once() -> None:
    """عند إضافة/تعديل/حذف معاملة نحدّث updated_at للعميل (للترتيب)."""
    global _customer_activity_listeners_registered
    if _customer_activity_listeners_registered:
        return
    from datetime import datetime

    from sqlalchemy import event, text

    from app_models.ledger import CustomerTransaction

    def _bump_customer(mapper, connection, target):
        connection.execute(
            text("UPDATE customers SET updated_at = :ts WHERE id = :cid"),
            {"cid": target.customer_id, "ts": datetime.utcnow()},
        )

    event.listen(CustomerTransaction, "after_insert", _bump_customer)
    event.listen(CustomerTransaction, "after_update", _bump_customer)
    event.listen(CustomerTransaction, "after_delete", _bump_customer)
    _customer_activity_listeners_registered = True
