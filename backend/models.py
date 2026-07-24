"""
FieldNotes — Database Models
SQLite MVP → PostgreSQL at scale
"""
import datetime
import os
from pathlib import Path

from sqlalchemy import create_engine, Column, String, Integer, DateTime, Boolean, Text, ForeignKey, Index, Enum as SQLEnum
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker
import enum

# Determine the repo root (this file lives in backend/)
BASE_DIR = Path(__file__).resolve().parent.parent

# Database URL from environment variable, default to SQLite
# Ensure SQLite path is absolute to avoid CWD issues — anchored at the REPO ROOT
# so the existing fieldnotes.db (created when uvicorn ran from the repo root) is preserved.
_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///fieldnotes.db")
# Normalize legacy "postgres://" scheme (Heroku/Railway-style) — SQLAlchemy 2.0 requires "postgresql://"
if _DATABASE_URL.startswith("postgres://"):
    _DATABASE_URL = "postgresql://" + _DATABASE_URL[len("postgres://"):]
if _DATABASE_URL.startswith("sqlite:///"):
    db_file = Path(_DATABASE_URL.replace("sqlite:///", ""))
    if not db_file.is_absolute():
        _DATABASE_URL = f"sqlite:///{BASE_DIR / db_file}"

DATABASE_URL = _DATABASE_URL
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}) if DATABASE_URL.startswith("sqlite:///") else create_engine(DATABASE_URL)
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
    stripe_customer_id = Column(String)      # Stripe customer (linked via webhook + signup email)
    stripe_subscription_id = Column(String)  # active Stripe subscription
    subscription_status = Column(String, default="none")  # none|trialing|active|past_due|canceled
    beta_all_access = Column(Boolean, default=True)  # P5: beta users get every feature; flip off post-beta
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    is_active = Column(Boolean, default=True)

    accounts = relationship("Account", back_populates="business", cascade="all, delete-orphan")
    workers = relationship("Worker", back_populates="business", cascade="all, delete-orphan")
    service_logs = relationship("ServiceLog", back_populates="business", cascade="all, delete-orphan")
    actions = relationship("Action", back_populates="business", cascade="all, delete-orphan")
    qa_events = relationship("QaEvent", back_populates="business", cascade="all, delete-orphan") # NEW

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
    gate_code = Column(String)        # gate/door/alarm codes (Ask FieldNotes answers from this)
    access_notes = Column(Text)       # parking, entry instructions, warnings (dogs, badges)
    schedule = Column(String)         # free-text: "Mon/Thu", "every other Tue", "1st of month"
    schedule_parsed = Column(Text)    # JSON normalized schedule (P4 route awareness)
    recap_enabled = Column(Boolean, default=False)   # P8: client recaps opt-in (default OFF)
    recap_email = Column(String)                     # P8: client's service-contact email (owner-confirmed)
    recap_auto_send = Column(Boolean, default=False) # P8 Phase 2 — built but INERT at launch (approve-first always)
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
    sms_opted_out = Column(Boolean, default=False)  # P3: STOP honored — NEVER text this number again until START
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    business = relationship("Business", back_populates="workers")
    service_logs = relationship("ServiceLog", back_populates="worker")
    qa_events = relationship("QaEvent", back_populates="worker") # NEW

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

class PendingSubscription(Base):
    """A Stripe checkout that completed before the business signed up.
    Linked to a Business at /onboarding/signup by owner_email."""
    __tablename__ = "pending_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, nullable=False, index=True)  # lowercased customer email
    plan = Column(String, default="team")  # solo|team|crew
    stripe_customer_id = Column(String)
    stripe_subscription_id = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class QaEvent(Base):
    """A record of a Q&A exchange via the Ask FieldNotes assistant."""
    __tablename__ = "qa_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    worker_id = Column(Integer, ForeignKey("workers.id"), nullable=True) # Can be None if owner asks
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    sources = Column(Text) # JSON string of sources used for the answer
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    business = relationship("Business", back_populates="qa_events")
    worker = relationship("Worker", back_populates="qa_events")

class AccountTask(Base):
    """A task attached to an account — the delegation loop (P7).

    Open tasks surface at log time, in the morning push, in Q&A, and on the
    dashboard. Unassigned tasks (assigned_worker_id NULL) are visible to the
    whole crew. Nothing auto-closes silently: ambiguous closes ask, implicit
    completions get a YES/NO confirm.
    """
    __tablename__ = "account_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    title = Column(String, nullable=False)            # "Repair pool cover"
    details = Column(Text)                            # "client said tear on deep-end corner"
    status = Column(String, default="open")           # open / done / cancelled
    assigned_worker_id = Column(Integer, ForeignKey("workers.id"), nullable=True)  # null = whole crew
    supplies_needed = Column(Text)                    # "cover patch kit, 12ft strap"
    due_date = Column(String)                         # raw day text ("Thursday") — surfacing doesn't depend on it
    source = Column(String)                           # chat_owner / chat_rep / dashboard
    created_by_worker_id = Column(Integer, ForeignKey("workers.id"), nullable=True)  # null = owner created
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    closed_at = Column(DateTime)
    closed_by_worker_id = Column(Integer, ForeignKey("workers.id"), nullable=True)

    __table_args__ = (
        Index("ix_account_tasks_scope", "business_id", "account_id", "status"),
    )


class RecapLog(Base):
    """P8 — client recap lifecycle. One row per client-facing recap.

    Status flow: drafting (LLM rewrite in flight) → pending_approval →
    sent / skipped. held = LLM failure, safety-filter rejection, tier gate,
    or email send failure — a held recap is NEVER sent as-is; the owner is
    pinged to handle it manually. The raw worker note must never reach a
    client (spec hard rule #1), so there is no raw-text fallback anywhere
    in this pipeline.

    Batching: notes for the same account within the visit window merge into
    ONE recap — source_log_ids (JSON) tracks every log folded in; service_log_id
    keeps the FIRST log for back-compat with the spec schema.
    """
    __tablename__ = "recap_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    service_log_id = Column(Integer, ForeignKey("service_logs.id"), nullable=False)  # first log
    source_log_ids = Column(Text)           # JSON list of every merged ServiceLog id
    source_text = Column(Text)              # combined note text the rewriter works from
    client_text = Column(Text)              # the client-safe rewrite (never raw note)
    status = Column(String, default="drafting")  # drafting/pending_approval/sent/skipped/held
    channel = Column(String, default="email")
    approved_by_worker_id = Column(Integer, ForeignKey("workers.id"), nullable=True)  # null = owner
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    sent_at = Column(DateTime)

    __table_args__ = (
        Index("ix_recap_log_scope", "business_id", "account_id", "status"),
    )


class PendingTaskClose(Base):
    """A proposed task close awaiting the rep's YES/NO (P7 implicit-completion
    confirm). Created when a logged note overlaps an open task's title. Never
    auto-closes — expired proposals (24h) are simply ignored."""
    __tablename__ = "pending_task_closes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    worker_id = Column(Integer, ForeignKey("workers.id"), nullable=False)  # who must confirm
    task_id = Column(Integer, ForeignKey("account_tasks.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_pending_task_closes_worker", "worker_id", "created_at"),
    )


def init_db():
    Base.metadata.create_all(bind=engine)

