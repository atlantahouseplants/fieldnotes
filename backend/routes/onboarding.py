"""
FieldNotes — Business Onboarding Routes
Create/manage business accounts + self-serve signup.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import List, Optional
import os
import re
import secrets

from ..models import SessionLocal, Business, Account, PendingSubscription

router = APIRouter(tags=["onboarding"])

BASE_URL = "https://fieldnotesapp.io"
BOT_USERNAME = "Field_notesbot_bot"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def slugify(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


class SignupRequest(BaseModel):
    business_name: str
    owner_name: str
    owner_email: EmailStr
    accounts: List[str] = []


@router.post("/onboarding/signup", status_code=201)
def signup(data: SignupRequest, db: Session = Depends(get_db)):
    """Self-serve signup: create business + accounts, return invite + dashboard links."""
    name = data.business_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Business name required")

    # Unique slug
    slug = slugify(name)
    n = 1
    while db.query(Business).filter(Business.slug == slug).first():
        n += 1
        slug = f"{slugify(name)}-{n}"

    biz = Business(
        name=name,
        slug=slug,
        owner_email=data.owner_email,
        owner_name=data.owner_name.strip(),
        dashboard_key=secrets.token_urlsafe(12),
        invite_token=secrets.token_urlsafe(12),
    )
    db.add(biz)
    db.flush()  # get biz.id

    # Link a Stripe subscription that checked out before signup (matched by email)
    pend = db.query(PendingSubscription).filter(
        PendingSubscription.email == data.owner_email.strip().lower()
    ).order_by(PendingSubscription.created_at.desc()).first()
    if pend:
        biz.stripe_customer_id = pend.stripe_customer_id
        biz.stripe_subscription_id = pend.stripe_subscription_id
        biz.subscription_status = "trialing"
        if pend.plan in ("solo", "team", "crew"):
            biz.tier = pend.plan
        db.delete(pend)

    # Create accounts from pasted list
    created = []
    seen = set()
    for raw in data.accounts[:100]:
        acc_name = raw.strip()
        if not acc_name or acc_name.lower() in seen:
            continue
        seen.add(acc_name.lower())
        db.add(Account(business_id=biz.id, name=acc_name, is_active=True))
        created.append(acc_name)

    db.commit()
    db.refresh(biz)

    # Alert the founder (fire-and-forget — never block a signup on this)
    founder_chat = os.getenv("FIELDNOTES_FOUNDER_CHAT_ID")
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if founder_chat and bot_token:
        try:
            import httpx
            plan_info = f" ({biz.tier} trial)" if biz.stripe_subscription_id else " (no checkout yet)"
            httpx.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": founder_chat, "parse_mode": "HTML",
                      "text": f"🌱 <b>New FieldNotes signup!</b>\n<b>{biz.name}</b> — {biz.owner_name}{plan_info}\n"
                              f"{len(created)} accounts · biz #{biz.id}"},
                timeout=5,
            )
        except Exception:
            pass

    return {
        "business_id": biz.id,
        "business_name": biz.name,
        "accounts_created": len(created),
        "invite_link": f"https://t.me/{BOT_USERNAME}?start=invite_{biz.invite_token}",
        "owner_link": f"https://t.me/{BOT_USERNAME}?start=owner_{biz.invite_token}",
        "dashboard_url": f"{BASE_URL}/app/dashboard.html?biz={biz.id}&key={biz.dashboard_key}",
    }


@router.get("/onboarding/invite/{token}")
def resolve_invite(token: str, db: Session = Depends(get_db)):
    """Validate an invite token (used by bot deep-link flow)."""
    biz = db.query(Business).filter(Business.invite_token == token).first()
    if not biz:
        raise HTTPException(status_code=404, detail="Invalid invite link")
    return {"business_id": biz.id, "business_name": biz.name}


# --- P2: CSV client-list import ---

from ..deps import verify_business_key
from ..services.csv_import import parse_csv_text, map_headers, map_headers_llm, import_accounts


class CsvImportRequest(BaseModel):
    business_id: int
    key: str
    csv_text: str


@router.post("/onboarding/import-csv")
async def import_csv(request: Request, db: Session = Depends(get_db)):
    """
    Import a client list with rich fields (address, gate codes, contacts, schedule).
    Accepts JSON {business_id, key, csv_text} OR multipart form (business_id, key, file).
    Key-locked per tenant. LLM maps HEADERS ONLY — data rows never leave the server.
    """
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        business_id = int(form.get("business_id", 0))
        key = str(form.get("key", ""))
        upload = form.get("file")
        if upload is None:
            raise HTTPException(status_code=400, detail="file field required")
        raw = await upload.read()
        csv_text = raw.decode("utf-8-sig", errors="replace")
    else:
        data = CsvImportRequest(**(await request.json()))
        business_id, key, csv_text = data.business_id, data.key, data.csv_text

    biz = verify_business_key(business_id, key, db)

    # P5: CSV import is a Team-tier feature
    from ..deps import require_feature
    require_feature(biz, "csv_import")

    headers, rows = parse_csv_text(csv_text)
    if not headers or not rows:
        raise HTTPException(status_code=400, detail="No CSV rows found — need a header row plus at least one data row")
    if len(rows) > 500:
        raise HTTPException(status_code=400, detail="Max 500 rows per import")

    header_mapping = await map_headers_llm(headers)
    result = import_accounts(db, biz.id, csv_text, header_mapping)
    result["business"] = biz.name
    return result


@router.get("/onboarding/import-template")
def import_template():
    """Downloadable CSV template for client-list imports."""
    from fastapi.responses import PlainTextResponse
    csv_body = (
        "name,address,gate_code,access_notes,contact_name,contact_phone,schedule,notes\n"
        "Riverside Office Park,1200 Riverside Pkwy Atlanta GA,4412,Loading dock B; sensor is touchy,Dana Whitfield,404-555-0182,Mon/Thu,Quarterly filter contract\n"
        "Grand Hotel Downtown,55 Peachtree St Atlanta GA,,Service elevator - get key from front desk,Luis,404-555-0143,Wed,No lobby work before 10am\n"
    )
    return PlainTextResponse(
        csv_body,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fieldnotes-clients-template.csv"},
    )


# --- Legacy business CRUD ---

from ..schemas import BusinessCreate, BusinessOut

@router.post("/businesses/", response_model=BusinessOut, status_code=201)
def create_business(data: BusinessCreate, db: Session = Depends(get_db)):
    slug = slugify(data.name)
    existing = db.query(Business).filter(Business.slug == slug).first()
    if existing:
        slug = f"{slug}-{existing.id + 1}" if existing else slug

    biz = Business(
        name=data.name,
        slug=slug,
        owner_email=data.owner_email,
        owner_name=data.owner_name,
        phone=data.phone,
        dashboard_key=secrets.token_urlsafe(12),
        invite_token=secrets.token_urlsafe(12),
    )
    db.add(biz)
    db.commit()
    db.refresh(biz)
    return biz


@router.get("/businesses/{business_id}", response_model=BusinessOut)
def get_business(business_id: int, db: Session = Depends(get_db)):
    biz = db.query(Business).filter(Business.id == business_id).first()
    if not biz:
        raise HTTPException(status_code=404, detail="Business not found")
    return biz
