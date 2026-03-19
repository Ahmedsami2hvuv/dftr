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
        ShareLink,
        CustomerCategory,
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

        # عمود صورة للمعاملات
        try:
            conn.execute(
                text("ALTER TABLE customer_transactions ADD COLUMN IF NOT EXISTS photo_file_id VARCHAR(255)")
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
