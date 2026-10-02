from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from clinically_anchored_api.api import (
    audit,
    check_ins,
    clinics,
    consents,
    drafts,
    health,
    messages,
    procedures,
    queue,
)
from clinically_anchored_api.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="Clinically Anchored API",
    description="Backend service: check-in intake, red-flag rules, summary generation, audit log.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(audit.router)
app.include_router(clinics.router)
app.include_router(procedures.router)
app.include_router(check_ins.router)
app.include_router(messages.router)
app.include_router(consents.router)
app.include_router(drafts.router)
app.include_router(queue.router)

if settings.environment == "development":
    from clinically_anchored_api.api import dev

    app.include_router(dev.router)
