# -*- coding: utf-8 -*-
"""ربط مستخدمين لعميل واحد منطقياً + طابور معاملات للموافقة"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Date, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime

from database import Base


class PartnerLink(Base):
    """دعوة ربط بين مستخدمين لعميل واحد (معكوس المعاملات عند الطرف الآخر)."""

    __tablename__ = "partner_links"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    inviter_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    invitee_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    inviter_customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    invitee_customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    status = Column(String(20), nullable=False, default="pending")  # pending, accepted, cancelled
    created_at = Column(DateTime, default=datetime.utcnow)


class PartnerPendingTx(Base):
    """معاملة بانتظار موافقة الطرف الآخر قبل النسخ المعكوس."""

    __tablename__ = "partner_pending_txs"
    __table_args__ = (UniqueConstraint("source_tx_id", name="uq_partner_pending_source_tx"),)

    id = Column(Integer, primary_key=True, index=True)
    partner_link_id = Column(Integer, ForeignKey("partner_links.id"), nullable=False, index=True)
    source_tx_id = Column(Integer, ForeignKey("customer_transactions.id"), nullable=False)
    status = Column(String(20), nullable=False, default="pending")  # pending, approved, rejected
    mirrored_tx_id = Column(Integer, ForeignKey("customer_transactions.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CustomerPaymentReminder(Base):
    """تذكير تسديد: تاريخ استحقاق + عدد أيام قبل التذكير."""

    __tablename__ = "customer_payment_reminders"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    due_date = Column(Date, nullable=False)
    # وقت الاستحقاق الكامل (اختياري ترحيلاً من due_date فقط)
    due_at = Column(DateTime, nullable=True)
    remind_before_days = Column(Integer, nullable=False, default=1)  # 0..5
    last_notified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
