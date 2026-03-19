# -*- coding: utf-8 -*-
"""سجلات الدفتر والديون + العملاء ومعاملاتهم"""
from sqlalchemy import Column, Integer, BigInteger, String, Numeric, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class Customer(Base):
    """عميل في دفتر الديون (للمستخدم الحالي)"""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    phone = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="customers")
    transactions = relationship(
        "CustomerTransaction",
        back_populates="customer",
        order_by="CustomerTransaction.created_at.desc()",
    )
    share_links = relationship("ShareLink", back_populates="customer")


class CustomerTransaction(Base):
    """معاملة مع عميل: أعطيت أو أخذت"""
    __tablename__ = "customer_transactions"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    kind = Column(String(10), nullable=False)  # "gave" أعطيت أو "took" أخذت
    note = Column(Text, nullable=True)
    photo_file_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer", back_populates="transactions")


class ShareLink(Base):
    """رابط مشاركة لعميل (لرؤية المعاملات)"""
    __tablename__ = "share_links"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    token = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer", back_populates="share_links")


class LedgerEntry(Base):
    """قيد في دفتر الحسابات (دخل أو مصروف)"""
    __tablename__ = "ledger_entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    kind = Column(String(20), nullable=False)  # "income" أو "expense"
    amount = Column(Numeric(15, 2), nullable=False)
    category = Column(String(50), nullable=True)  # fixed_salary / additional_income / expenses
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="ledger_entries")


class Debt(Base):
    """دين: من مستخدم لآخر أو لشخص خارجي"""
    __tablename__ = "debts"

    id = Column(Integer, primary_key=True, index=True)
    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    to_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    to_name = Column(String(255), nullable=True)
    amount = Column(Numeric(15, 2), nullable=False)
    is_they_owe_me = Column(Integer, default=1)  # 1 = هم مدينون لي، 0 = أنا مدين
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    from_user = relationship("User", foreign_keys=[from_user_id], back_populates="debts_given")
    to_user = relationship("User", foreign_keys=[to_user_id], back_populates="debts_received")
