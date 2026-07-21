"""
FieldNotes — FastAPI Application
"""
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
import os

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

from .models import init_db, SessionLocal, engine, DATABASE_URL
from sqlalchemy import text, inspect
from pathlib import Path
from .routes import accounts, workers, logs, webhook, summary, businesses as routes, onboarding, billing, hiring, dashboard_api

app = FastAPI(
    title="FieldNotes",
    version="0.1.0",
    description="Voice-first field service logging for service businesses"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(accounts.router)
app.include_router(workers.router)
app.include_router(logs.router)
app.include_router(webhook.router)
app.include_router(summary.router)
app.include_router(routes.router)
app.include_router(onboarding.router)
app.include_router(billing.router)
app.include_router(hiring.router)
app.include_router(dashboard_api.router)

# Serve frontend dashboard
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_dir):
    app.mount("/app", StaticFiles(directory=frontend_dir, html=True), name="frontend")


def _alembic_config():
    """Programmatic Alembic config — env.py resolves DATABASE_URL the same way the app does."""
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "alembic"))
    return cfg


@app.on_event("startup")
async def startup():
    """Schema management: Alembic owns Postgres; create_all is fine for local SQLite.

    Three states handled uniformly:
      - fresh DB (no tables)        → alembic upgrade head (PG) / create_all (sqlite)
      - legacy DB (tables, no alembic_version) → stamp head, then upgrade
      - migrated DB                 → upgrade head (applies any pending migrations)
    """
    from alembic import command
    insp = inspect(engine)
    has_tables = insp.has_table("businesses")
    has_alembic = insp.has_table("alembic_version")
    if DATABASE_URL.startswith("sqlite"):
        init_db()
        if has_tables and not has_alembic:
            command.stamp(_alembic_config(), "head")
    else:
        if has_tables and not has_alembic:
            # PG created by pre-Alembic create_all — adopt it instead of crashing on re-CREATE
            command.stamp(_alembic_config(), "head")
        command.upgrade(_alembic_config(), "head")


@app.get("/")
async def root():
    """Root → marketing landing page. API status lives at /health."""
    return RedirectResponse(url="/app/index.html")


@app.get("/health", status_code=200)
async def health(response: Response):
    db_status = "down"
    db = None  # Initialize db to None
    try:
        db = SessionLocal()
        # Perform a trivial query to check connectivity
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        print(f"Database connection failed: {e}")
        response.status_code = 500
    finally:
        if db: # Only close if db was successfully created
            db.close()

    return {"status": "healthy", "db": db_status}
