"""
models.py — Modelos SQLAlchemy de PiScan.
Cambiar de SQLite a otro motor: solo cambiar PISCAN_DB_URL, nada mas.
"""
import os
from datetime import datetime
from sqlalchemy import create_engine, String, Integer, Text, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DB_PATH = os.environ.get("PISCAN_DB_PATH", os.path.expanduser("~/piscanlvgl/data/piscan.db"))
DB_URL  = os.environ.get("PISCAN_DB_URL", f"sqlite:///{DB_PATH}")

class Base(DeclarativeBase):
    pass

class Config(Base):
    __tablename__ = "config"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class SecurityCredential(Base):
    __tablename__ = "security_credentials"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cred_type: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(128), nullable=False)
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class PinConfig(Base):
    __tablename__ = "pin_config"
    pin_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    gpio_number: Mapped[int] = mapped_column(Integer, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)

class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_type: Mapped[str] = mapped_column(String(64), nullable=False)
    identifier: Mapped[str] = mapped_column(String(128), nullable=True)
    friendly_name: Mapped[str] = mapped_column(String(128), nullable=True)
    managed_by_os: Mapped[int] = mapped_column(Integer, default=1)
    metadata_json: Mapped[str] = mapped_column("metadata", Text, nullable=True)

class Action(Base):
    __tablename__ = "actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=True)
    enabled: Mapped[int] = mapped_column(Integer, default=1)

_engine = None
_SessionLocal = None

def get_engine():
    global _engine
    if _engine is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _engine = create_engine(DB_URL, future=True)
        Base.metadata.create_all(_engine)
    return _engine

def get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), future=True)
    return _SessionLocal()
