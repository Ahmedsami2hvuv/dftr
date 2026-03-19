# -*- coding: utf-8 -*-
from sqlalchemy import Column, Integer, BigInteger, String, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    phone = Column(String(32), nullable=True)
    full_name = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    ledger_entries = relationship("LedgerEntry", back_populates="user")
    debts_given = relationship("Debt", foreign_keys="Debt.from_user_id", back_populates="from_user")
    debts_received = relationship("Debt", foreign_keys="Debt.to_user_id", back_populates="to_user")
