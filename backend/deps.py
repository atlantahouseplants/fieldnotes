"""
FieldNotes — shared dependencies
"""
import secrets
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import Business


def verify_business_key(business_id: int, key: str, db: Session) -> Business:
    """Verify the per-business dashboard key. Raises 403/404 on failure."""
    biz = db.query(Business).filter(Business.id == business_id).first()
    if not biz:
        raise HTTPException(status_code=404, detail="Business not found")
    if not biz.dashboard_key or not secrets.compare_digest(biz.dashboard_key, key or ""):
        raise HTTPException(status_code=403, detail="Invalid dashboard key")
    return biz


# ── P5: tiering & feature gates ──────────────────────────────────
# Tier truth comes from the Stripe webhook linkage (Business.tier).
# Beta override: Business.beta_all_access=True → every feature passes.

TIER_ORDER = {"solo": 0, "team": 1, "crew": 2}

FEATURE_TIERS = {
    "qa": "team",           # Ask FieldNotes Q&A
    "csv_import": "team",   # rich client-list import
    "routes": "crew",       # route awareness (route today / missed stops)
    "sms": "crew",          # SMS channel (P3, not yet built)
    "morning_push": "crew", # morning route push
    "recaps": "team",       # P8 client recaps (email; SMS recaps ride the sms gate post-P3)
}

FEATURE_LABELS = {
    "qa": ("Ask FieldNotes (Q&A)", "Team", "$79/mo"),
    "csv_import": ("Client list import", "Team", "$79/mo"),
    "routes": ("Route awareness", "Crew", "$149/mo"),
    "sms": ("SMS channel", "Crew", "$149/mo"),
    "morning_push": ("Morning route push", "Crew", "$149/mo"),
    "recaps": ("Client recaps", "Team", "$79/mo"),
}

UPGRADE_URL = "https://fieldnotesapp.io/app/pricing.html"


def has_feature(biz: Business, feature: str) -> bool:
    """True if the business's tier (or beta flag) unlocks the feature."""
    if getattr(biz, "beta_all_access", False):
        return True
    required = FEATURE_TIERS.get(feature)
    if required is None:
        return True  # unknown feature = ungated (v1 product)
    return TIER_ORDER.get((biz.tier or "solo").lower(), 0) >= TIER_ORDER[required]


def upgrade_detail(feature: str, biz: Business) -> dict:
    label, tier_name, price = FEATURE_LABELS.get(feature, (feature, "Team", "$79/mo"))
    return {
        "error": "feature_gated",
        "feature": feature,
        "label": label,
        "required_tier": FEATURE_TIERS.get(feature),
        "current_tier": biz.tier or "solo",
        "message": f"{label} is on the {tier_name} plan ({price}).",
        "upgrade_url": UPGRADE_URL,
    }


def require_feature(biz: Business, feature: str) -> None:
    """Raise HTTP 402 with an upgrade message when the feature is gated."""
    if not has_feature(biz, feature):
        raise HTTPException(status_code=402, detail=upgrade_detail(feature, biz))


def upgrade_message(feature: str, biz: Business) -> str:
    """Telegram-friendly upgrade prompt for a gated attempt."""
    d = upgrade_detail(feature, biz)
    return (
        f"🔒 <b>{d['label']}</b> is on the <b>{d['required_tier'].capitalize()}</b> plan.\n\n"
        f"Ask the boss to upgrade at {d['upgrade_url']} — "
        f"it takes about a minute and unlocks instantly."
    )
