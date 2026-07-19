"""
FieldNotes — Stripe Billing Integration
Checkout sessions, signed webhook, subscription → business linkage.
"""
from fastapi import APIRouter, HTTPException, Request
import hashlib
import hmac
import httpx
import json
import os
import time

from ..models import SessionLocal, Business, PendingSubscription

STRIPE_SECRET = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_SOLO = os.getenv("STRIPE_PRICE_SOLO", "price_solo_39")
STRIPE_PRICE_TEAM = os.getenv("STRIPE_PRICE_TEAM", "price_team_79")
STRIPE_PRICE_CREW = os.getenv("STRIPE_PRICE_CREW", "price_crew_149")

router = APIRouter(prefix="/billing", tags=["billing"])

VALID_PLANS = ("solo", "team", "crew")


@router.post("/checkout")
async def create_checkout_session(plan: str = "team"):
    """Create a Stripe checkout session. Redirects user to Stripe."""
    if not STRIPE_SECRET:
        return {"error": "Stripe not configured yet", "mode": "demo"}

    if plan not in VALID_PLANS:
        plan = "team"
    price_id = {"solo": STRIPE_PRICE_SOLO, "team": STRIPE_PRICE_TEAM, "crew": STRIPE_PRICE_CREW}[plan]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.stripe.com/v1/checkout/sessions",
            auth=(STRIPE_SECRET, ""),
            data={
                "mode": "subscription",
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": 1,
                "subscription_data[trial_period_days]": "30",
                "metadata[plan]": plan,
                "subscription_data[metadata][plan]": plan,
                "success_url": "https://fieldnotesapp.io/app/start.html",
                "cancel_url": "https://fieldnotesapp.io/app/pricing.html",
                "allow_promotion_codes": "true",
                "billing_address_collection": "auto",
            },
        )
        data = resp.json()
        if resp.status_code == 200:
            return {"url": data["url"]}
        raise HTTPException(status_code=400, detail=data)


# ── Webhook signature verification ─────────────────────

def _verify_stripe_signature(payload: bytes, sig_header: str, tolerance: int = 300) -> bool:
    """Manual Stripe-Signature verification (HMAC-SHA256 of '{ts}.{payload}')."""
    if not STRIPE_WEBHOOK_SECRET or not sig_header:
        return False
    try:
        parts = {}
        for item in sig_header.split(","):
            k, _, v = item.partition("=")
            parts.setdefault(k, []).append(v)
        ts = parts["t"][0]
        v1_sigs = parts.get("v1", [])
        if not v1_sigs:
            return False
        if abs(time.time() - int(ts)) > tolerance:
            return False
        signed = ts.encode() + b"." + payload
        expected = hmac.new(STRIPE_WEBHOOK_SECRET.encode(), signed, hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, s) for s in v1_sigs)
    except (KeyError, IndexError, ValueError):
        return False


def _link_business_by_email(db, email: str, cust_id: str, sub_id: str, plan: str, status: str) -> bool:
    """Attach stripe IDs to an existing Business by owner_email. Returns True if linked."""
    biz = db.query(Business).filter(Business.owner_email.ilike(email)).first()
    if not biz:
        return False
    biz.stripe_customer_id = cust_id
    biz.stripe_subscription_id = sub_id
    biz.subscription_status = status
    if plan in VALID_PLANS:
        biz.tier = plan
    return True


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Stripe event receiver. Verifies signature, then:
    - checkout.session.completed  → park PendingSubscription by email (or link directly)
    - customer.subscription.updated/deleted → update Business status
    - invoice.payment_failed → mark past_due
    """
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if not _verify_stripe_signature(payload, sig):
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid payload")

    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    db = SessionLocal()
    try:
        if etype == "checkout.session.completed":
            email = (obj.get("customer_details") or {}).get("email") or obj.get("customer_email") or ""
            email = email.strip().lower()
            plan = (obj.get("metadata") or {}).get("plan", "team")
            cust_id = obj.get("customer", "")
            sub_id = obj.get("subscription", "")
            if email:
                if not _link_business_by_email(db, email, cust_id, sub_id, plan, "trialing"):
                    db.add(PendingSubscription(
                        email=email, plan=plan,
                        stripe_customer_id=cust_id, stripe_subscription_id=sub_id,
                    ))
                db.commit()

        elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
            sub_id = obj.get("id", "")
            status = "canceled" if etype.endswith("deleted") else obj.get("status", "active")
            biz = db.query(Business).filter(Business.stripe_subscription_id == sub_id).first()
            if biz:
                biz.subscription_status = status
                plan = (obj.get("metadata") or {}).get("plan")
                if plan in VALID_PLANS:
                    biz.tier = plan
                db.commit()
            else:
                # Subscription may still be parked as pending — update it there too
                pend = db.query(PendingSubscription).filter(
                    PendingSubscription.stripe_subscription_id == sub_id).first()
                if pend:
                    plan = (obj.get("metadata") or {}).get("plan")
                    if plan in VALID_PLANS:
                        pend.plan = plan
                    db.commit()

        elif etype == "invoice.payment_failed":
            sub_id = obj.get("subscription", "")
            biz = db.query(Business).filter(Business.stripe_subscription_id == sub_id).first()
            if biz:
                biz.subscription_status = "past_due"
                db.commit()
    finally:
        db.close()

    return {"received": True}
