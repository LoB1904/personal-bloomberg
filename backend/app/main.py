"""
Personal Bloomberg — FastAPI application entry point.

Avvio:
    uvicorn app.main:app --reload --port 8000

Docs interattive:
    http://localhost:8000/docs
"""
from fastapi import FastAPI

from app.api.endpoints import router

app = FastAPI(
    title="Personal Bloomberg",
    description="Analytics e paper trading personale",
    version="2.0.0",
)

app.include_router(router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
