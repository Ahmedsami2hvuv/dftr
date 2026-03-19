# -*- coding: utf-8 -*-
"""سجلات الدفتر والديون"""
from sqlalchemy import Column, Integer, BigInteger, String, Numeric, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class LedgerEntry(Base):
    """قيد في دفتر الحسابات (دخل أو مصروف)"""
    __tablename__ = "ledger_entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    kind = Column(String(20), nullable=False)  # "income" أو "expense"
    amount = Column(Numeric(15, 2), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="ledger_entries")


class Debt(Base):
    """دين: من مستخدم لآخر أو لشخص خارجي"""
    __tablename__ = "debts"

    id = Column(Integer, primary_key=True, index=True)
    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)  # صاحب السجل
    to_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)   # مستخدم آخر أو null لشخص خارجي
    to_name = Column(String(255), nullable=True)   # اسم الدائن/المدين إذا خارجي
    amount = Column(Numeric(15, 2), nullable=False)
    is_they_owe_me = Column(Integer, default=1)   # 1 = هم مدينون لي، 0 = أنا مدين
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    from_user = relationship("User", foreign_keys=[from_user_id], back_populates="debts_given")
    to_user = relationship("User", foreign_keys=[to_user_id], back_populates="debts_received")
