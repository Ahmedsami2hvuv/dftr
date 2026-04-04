# -*- coding: utf-8 -*-
from sqlalchemy import Column, Integer, BigInteger, String, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, nullable=True, index=True)
    username = Column(String(255), nullable=True)
    phone = Column(String(32), nullable=True)
    full_name = Column(String(255), nullable=True)
    password_hash = Column(String(128), nullable=True)
    reset_code = Column(String(10), nullable=True)
    reset_code_expires = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # تفضيلات المظهر: light, dark, auto
    theme_preference = Column(String(20), default="light")

    ledger_entries = relationship("LedgerEntry", back_populates="user")
    ledger_categories = relationship("LedgerCategory", back_populates="user")
    debts_given = relationship("Debt", foreign_keys="Debt.from_user_id", back_populates="from_user")
    debts_received = relationship("Debt", foreign_keys="Debt.to_user_id", back_populates="to_user")
    customers = relationship("Customer", back_populates="user")
