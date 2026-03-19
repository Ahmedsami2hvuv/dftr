# -*- coding: utf-8 -*-
"""إعدادات/أصول عامة للموقع (مثلاً شعار صفحة المشاركة)."""
from datetime import datetime

from sqlalchemy import Column, DateTime, LargeBinary, String

from database import Base


class SiteSetting(Base):
    """قيم ثنائية مرتبطة بمفتاح (مثل صورة الشعار)."""

    __tablename__ = "site_settings"

    key = Column(String(64), primary_key=True)
    blob_value = Column(LargeBinary, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# مفتاح ثابت لصورة الشعار في صفحة /creditbook/balance
BRAND_LOGO_SETTING_KEY = "brand_logo_image"
