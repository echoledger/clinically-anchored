from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness/readiness check for Railway and uptime monitoring."""
    return {"status": "ok"}
