"""FastAPI app factory. Run with: uvicorn ambient_ai.gateway.app:create_app --factory"""

from fastapi import FastAPI

from ambient_ai.gateway.webhook import router


def create_app() -> FastAPI:
    app = FastAPI(title="Ambient AI gateway")
    app.include_router(router)
    return app
