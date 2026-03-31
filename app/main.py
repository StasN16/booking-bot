from fastapi import FastAPI
from app.api.v1 import webhook

app = FastAPI(title="Booking Bot", version="0.1.0")

app.include_router(webhook.router, prefix="/api/v1")

@app.get("/")
async def root():
    return {"status": "ok", "message": "Bot is running!"}

@app.get("/health")
async def health():
    return {"status": "healthy"}