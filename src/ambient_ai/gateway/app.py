"""FastAPI app factory. Run with: uvicorn ambient_ai.gateway.app:create_app --factory"""

from fastapi import FastAPI

from ambient_ai.gateway.portal import router as portal_router
from ambient_ai.gateway.webhook import router as webhook_router


def create_app() -> FastAPI:
    app = FastAPI(title="Ambient AI gateway")
    app.include_router(webhook_router)
    app.include_router(portal_router)
    return app
