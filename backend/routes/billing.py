"""
FieldNotes — Stripe Billing Integration
Handles checkout sessions and subscription management.
"""
from fastapi import APIRouter, HTTPException
import httpx
import os

STRIPE_SECRET = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_SOLO = os.getenv("STRIPE_PRICE_SOLO", "price_solo_39")
STRIPE_PRICE_TEAM = os.getenv("STRIPE_PRICE_TEAM", "price_team_79")

router = APIRouter(prefix="/billing", tags=["billing"])


@router.post("/checkout")
async def create_checkout_session(plan: str = "team"):
    """Create a Stripe checkout session. Redirects user to Stripe."""
    if not STRIPE_SECRET:
        return {"error": "Stripe not configured yet", "mode": "demo"}

    price_id = STRIPE_PRICE_TEAM if plan == "team" else STRIPE_PRICE_SOLO

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.stripe.com/v1/checkout/sessions",
            auth=(STRIPE_SECRET, ""),
            data={
                "mode": "subscription",
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": 1,
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
