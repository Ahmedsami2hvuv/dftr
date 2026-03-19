# -*- coding: utf-8 -*-
"""اتصال قاعدة البيانات PostgreSQL (Railway)"""
from sqlalchemy import create_engine
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
    """إنشاء الجداول عند أول تشغيل"""
    from app_models import User, LedgerEntry, Debt  # noqa: F401
    Base.metadata.create_all(bind=engine)
