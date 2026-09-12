"""FastAPI app factory. Run with: uvicorn ambient_ai.gateway.app:create_app --factory"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from ambient_ai.gateway.portal import router as portal_router
from ambient_ai.gateway.telegram_webhook import register_bot
from ambient_ai.gateway.telegram_webhook import router as telegram_router
from ambient_ai.gateway.webhook import router as webhook_router


@asynccontextmanager
async def _lifespan(app: FastAPI):
    register_bot()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Ambient AI gateway", lifespan=_lifespan)
    app.include_router(webhook_router)
    app.include_router(telegram_router)
    app.include_router(portal_router)
    return app
