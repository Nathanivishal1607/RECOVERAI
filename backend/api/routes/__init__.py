"""Phase 5 + Phase 6 HTTP routes."""

from backend.api.routes.dashboard import router as dashboard_router
from backend.api.routes.recovery import router as recovery_router

__all__ = ["recovery_router", "dashboard_router"]
