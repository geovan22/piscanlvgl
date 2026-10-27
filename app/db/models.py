"""
models.py — Modelos SQLAlchemy de PiScan.
Cambiar de SQLite a otro motor: solo cambiar PISCAN_DB_URL, nada mas.
"""
import os
from datetime import datetime
from sqlalchemy import create_engine, String, Integer, Text, DateTime, Float
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

class WifiScanLog(Base):
    __tablename__ = "wifi_scan_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ssid: Mapped[str] = mapped_column(String(64), nullable=True)
    bssid: Mapped[str] = mapped_column(String(24), nullable=True)
    channel: Mapped[int] = mapped_column(Integer, nullable=True)
    security: Mapped[str] = mapped_column(String(16), nullable=True)
    signal: Mapped[float] = mapped_column(Float, nullable=True)

class WifiAttackLog(Base):
    __tablename__ = "wifi_attack_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    attack_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_ssid: Mapped[str] = mapped_column(String(64), nullable=True)
    target_bssid: Mapped[str] = mapped_column(String(24), nullable=True)
    target_client_mac: Mapped[str] = mapped_column(String(24), nullable=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=True)

class HoneypotLog(Base):
    """Telemetria de dispositivos que caen en el honeypot (AP abierto +
    portal informativo). SIN credenciales: solo datos para analisis/estadistica
    en una auditoria autorizada. Una fila por cliente observado por sesion."""
    __tablename__ = "honeypot_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ssid: Mapped[str] = mapped_column(String(64), nullable=True)          # nombre del honeypot
    client_mac: Mapped[str] = mapped_column(String(24), nullable=True)
    vendor: Mapped[str] = mapped_column(String(32), nullable=True)        # OUI -> fabricante
    client_ip: Mapped[str] = mapped_column(String(24), nullable=True)
    hostname: Mapped[str] = mapped_column(String(64), nullable=True)      # del DHCP
    os_guess: Mapped[str] = mapped_column(String(24), nullable=True)      # Android/iOS/Windows/...
    user_agent: Mapped[str] = mapped_column(String(256), nullable=True)
    requests: Mapped[int] = mapped_column(Integer, nullable=True)         # nro de hits al portal
    portal_user: Mapped[str] = mapped_column(String(64), nullable=True)   # usuario ingresado en el portal
    portal_pass: Mapped[str] = mapped_column(String(64), nullable=True)   # clave ingresada en el portal
    domains: Mapped[str] = mapped_column(Text, nullable=True)             # dominios DNS consultados
    # Telemetria extendida para auditoria (2026-10-15):
    rssi: Mapped[int] = mapped_column(Integer, nullable=True)             # #1 senal dBm (iw station dump)
    bytes_rx: Mapped[int] = mapped_column(Integer, nullable=True)         # #1 bytes recibidos por el cliente
    bytes_tx: Mapped[int] = mapped_column(Integer, nullable=True)         # #1 bytes enviados por el cliente
    dwell_s: Mapped[int] = mapped_column(Integer, nullable=True)          # #2 tiempo conectado (seg)
    dhcp_vendor: Mapped[str] = mapped_column(String(64), nullable=True)   # #3 vendor class DHCP (android-dhcp-14, MSFT 5.0)
    dhcp_fingerprint: Mapped[str] = mapped_column(String(128), nullable=True)  # #3 requested options DHCP
    sni_hosts: Mapped[str] = mapped_column(Text, nullable=True)           # #4 hosts TLS SNI (sitios HTTPS visitados)
    mdns_name: Mapped[str] = mapped_column(String(96), nullable=True)     # #6 nombre .local anunciado (mDNS)
    probe_ssids: Mapped[str] = mapped_column(Text, nullable=True)         # #5 PNL: redes que el equipo recuerda (probe req)
    pcap_path: Mapped[str] = mapped_column(String(160), nullable=True)    # #7 ruta del .pcap de la sesion
    details: Mapped[str] = mapped_column(Text, nullable=True)

class BluetoothLog(Base):
    """Dispositivos Bluetooth descubiertos (BR/EDR + LE). Una fila por device
    observado en una sesion de scan. Marco: laboratorio / pruebas autorizadas."""
    __tablename__ = "bluetooth_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    scan_type: Mapped[str] = mapped_column(String(16), nullable=True)     # classic | le | dual
    iface: Mapped[str] = mapped_column(String(16), nullable=True)         # hci0 / hci1
    mac: Mapped[str] = mapped_column(String(24), nullable=True)
    addr_type: Mapped[str] = mapped_column(String(16), nullable=True)     # public | random
    name: Mapped[str] = mapped_column(String(96), nullable=True)
    vendor: Mapped[str] = mapped_column(String(32), nullable=True)        # OUI -> fabricante
    rssi: Mapped[int] = mapped_column(Integer, nullable=True)             # dBm
    tx_power: Mapped[int] = mapped_column(Integer, nullable=True)         # dBm (BLE adv)
    appearance: Mapped[str] = mapped_column(String(48), nullable=True)    # BLE appearance (phone, ...)
    cls: Mapped[str] = mapped_column(String(16), nullable=True)           # Class of Device (BR/EDR)
    icon: Mapped[str] = mapped_column(String(32), nullable=True)          # bluez icon string
    services: Mapped[str] = mapped_column(Text, nullable=True)            # lista de UUIDs (coma)
    manufacturer_data: Mapped[str] = mapped_column(Text, nullable=True)   # company_id -> hex
    details: Mapped[str] = mapped_column(Text, nullable=True)

_engine = None
_SessionLocal = None

def _migrate_sqlite(engine):
    """Migracion minima para SQLite: create_all() crea TABLAS que faltan pero NO
    agrega COLUMNAS nuevas a tablas ya existentes. Recorremos cada modelo y
    hacemos ALTER TABLE ADD COLUMN por cada columna que falte. Best-effort: si
    algo falla no debe tumbar el arranque."""
    try:
        from sqlalchemy import inspect, text
        insp = inspect(engine)
        existing_tables = set(insp.get_table_names())
        type_map = {Integer: "INTEGER", Float: "REAL"}  # el resto -> TEXT
        with engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if table.name not in existing_tables:
                    continue  # tabla nueva: ya la creo create_all
                have = {c["name"] for c in insp.get_columns(table.name)}
                for col in table.columns:
                    if col.name in have:
                        continue
                    sqltype = next((v for k, v in type_map.items()
                                    if isinstance(col.type, k)), "TEXT")
                    conn.execute(text(
                        f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {sqltype}'))
    except Exception:
        pass

def get_engine():
    global _engine
    if _engine is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _engine = create_engine(DB_URL, future=True)
        Base.metadata.create_all(_engine)
        _migrate_sqlite(_engine)
    return _engine

def get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), future=True)
    return _SessionLocal()
