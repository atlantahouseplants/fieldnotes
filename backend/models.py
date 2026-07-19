"""
FieldNotes — Database Models
SQLite MVP → PostgreSQL at scale
"""
import datetime
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Boolean, Text, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker
import enum

DATABASE_URL = "sqlite:///fieldnotes.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

# ── Enums ──────────────────────────────────────────────

class ActionStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"

class ActionPriority(str, enum.Enum):
    urgent = "urgent"
    this_week = "this_week"
    next_visit = "next_visit"

class SubscriptionTier(str, enum.Enum):
    solo = "solo"
    team = "team"
    crew = "crew"

# ── Models ─────────────────────────────────────────────

class Business(Base):
    __tablename__ = "businesses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False)
    owner_email = Column(String, nullable=False)
    owner_name = Column(String, nullable=False)
    phone = Column(String)
    tier = Column(String, default="solo")
    dashboard_key = Column(String)  # secret token for dashboard access
    invite_token = Column(String)   # secret token for worker invite links
    owner_telegram_id = Column(String)  # owner's Telegram chat ID (daily summaries)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    is_active = Column(Boolean, default=True)

    accounts = relationship("Account", back_populates="business", cascade="all, delete-orphan")
    workers = relationship("Worker", back_populates="business", cascade="all, delete-orphan")
    service_logs = relationship("ServiceLog", back_populates="business", cascade="all, delete-orphan")
    actions = relationship("Action", back_populates="business", cascade="all, delete-orphan")

class Account(Base):
    """A client location/job site the business services."""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    name = Column(String, nullable=False)
    shorthand = Column(String)  # e.g., "acme", "perkins" — what workers say
    address = Column(String)
    contact_name = Column(String)
    contact_phone = Column(String)
    notes = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    business = relationship("Business", back_populates="accounts")
    service_logs = relationship("ServiceLog", back_populates="account")
    route_entries = relationship("RouteEntry", back_populates="account")

class Worker(Base):
    """A field worker who sends notes."""
    __tablename__ = "workers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    name = Column(String, nullable=False)
    telegram_id = Column(String, unique=True)
    phone = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    business = relationship("Business", back_populates="workers")
    service_logs = relationship("ServiceLog", back_populates="worker")

class ServiceLog(Base):
    """A single service stop logged by a worker."""
    __tablename__ = "service_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)  # nullable for uncategorized
    worker_id = Column(Integer, ForeignKey("workers.id"), nullable=False)
    raw_note = Column(Text, nullable=False)           # original worker message
    parsed_status = Column(String)                     # "all good", "issues found", etc.
    parsed_issues = Column(Text)                       # extracted problems
    parsed_supplies = Column(Text)                     # supplies needed
    parsed_followups = Column(Text)                    # follow-up actions
    parsed_customer_requests = Column(Text)            # client asked for something
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    processing_time_ms = Column(Integer)               # how long AI took

    business = relationship("Business", back_populates="service_logs")
    account = relationship("Account", back_populates="service_logs")
    worker = relationship("Worker", back_populates="service_logs")

    def get_issues(self):
        import json
        try:
            return json.loads(self.parsed_issues) if self.parsed_issues else []
        except (json.JSONDecodeError, TypeError):
            return []

    def get_supplies(self):
        import json
        try:
            return json.loads(self.parsed_supplies) if self.parsed_supplies else []
        except (json.JSONDecodeError, TypeError):
            return []

    def get_followups(self):
        import json
        try:
            return json.loads(self.parsed_followups) if self.parsed_followups else []
        except (json.JSONDecodeError, TypeError):
            return []

    def account_name(self):
        if self.account:
            return self.account.name
        return "uncategorized"

class Action(Base):
    """An action item generated from a service log."""
    __tablename__ = "actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    service_log_id = Column(Integer, ForeignKey("service_logs.id"))
    account_id = Column(Integer, ForeignKey("accounts.id"))
    description = Column(String, nullable=False)
    priority = Column(String, default="this_week")
    status = Column(String, default="pending")
    source = Column(String)  # "service_log", "manual", "missed_stop"
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime)

    business = relationship("Business", back_populates="actions")

class RouteEntry(Base):
    """A scheduled stop — used for missed-stop detection."""
    __tablename__ = "route_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    day_of_week = Column(String, nullable=False)  # "monday", "tuesday", etc.
    week_type = Column(String, default="weekly")  # "weekly", "week_a", "week_b"
    route_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

    account = relationship("Account", back_populates="route_entries")

def init_db():
    Base.metadata.create_all(bind=engine)
