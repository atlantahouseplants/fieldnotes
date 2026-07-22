"""
FieldNotes — Pydantic Schemas
"""
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime


# ── Business ───────────────────────────────────────────

class BusinessCreate(BaseModel):
    name: str
    owner_email: EmailStr
    owner_name: str
    phone: Optional[str] = None

class BusinessOut(BaseModel):
    id: int
    name: str
    slug: str
    owner_email: str
    owner_name: str
    tier: str
    created_at: datetime
    is_active: bool

    model_config = {"from_attributes": True}


# ── Account ────────────────────────────────────────────

class AccountCreate(BaseModel):
    name: str
    shorthand: Optional[str] = None
    address: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    notes: Optional[str] = None

class AccountOut(BaseModel):
    id: int
    business_id: int
    name: str
    shorthand: Optional[str]
    address: Optional[str]
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    gate_code: Optional[str] = None
    access_notes: Optional[str] = None
    schedule: Optional[str] = None
    notes: Optional[str] = None
    recap_enabled: Optional[bool] = False
    recap_email: Optional[str] = None
    is_active: bool

    model_config = {"from_attributes": True}


# ── Worker ─────────────────────────────────────────────

class WorkerCreate(BaseModel):
    name: str
    telegram_id: Optional[str] = None
    phone: Optional[str] = None

class WorkerOut(BaseModel):
    id: int
    business_id: int
    name: str
    telegram_id: Optional[str]
    is_active: bool

    model_config = {"from_attributes": True}


# ── Service Log ────────────────────────────────────────

class ServiceLogOut(BaseModel):
    id: int
    account_id: Optional[int]  # nullable: uncategorized notes save without an account
    worker_id: int
    raw_note: str
    parsed_status: Optional[str]
    parsed_issues: Optional[str]
    parsed_supplies: Optional[str]
    parsed_followups: Optional[str]
    timestamp: datetime
    processing_time_ms: Optional[int]

    model_config = {"from_attributes": True}


# ── Action ─────────────────────────────────────────────

class ActionCreate(BaseModel):
    account_id: Optional[int] = None
    description: str
    priority: str = "this_week"
    source: str = "manual"

class ActionUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    description: Optional[str] = None

class ActionOut(BaseModel):
    id: int
    business_id: int
    account_id: Optional[int]
    description: str
    priority: str
    status: str
    source: str
    created_at: datetime
    completed_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ── Route ──────────────────────────────────────────────

class RouteEntryCreate(BaseModel):
    account_id: int
    day_of_week: str
    week_type: str = "weekly"
    route_order: int = 0

class RouteEntryOut(BaseModel):
    id: int
    business_id: int
    account_id: int
    day_of_week: str
    week_type: str
    route_order: int
    is_active: bool

    model_config = {"from_attributes": True}


# ── Daily Summary ──────────────────────────────────────

class DailySummary(BaseModel):
    date: str
    business_name: str
    stops_completed: int
    stops_expected: int
    stops_missed: List[str]  # account names
    issues_flagged: int
    actions_pending: int
    supplies_needed: List[str]
    workers_active: List[str]
    recaps_pending: int = 0  # P8: client recaps waiting on owner approval


# ── Telegram Webhook ───────────────────────────────────

class TelegramMessage(BaseModel):
    """Incoming worker note — received from Telegram webhook."""
    worker_telegram_id: str
    text: str  # the voice transcription or typed message
    timestamp: Optional[datetime] = None
