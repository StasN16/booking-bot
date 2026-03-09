from fastapi import FastAPI

app = FastAPI(title="Booking Bot", version="0.1.0")

@app.get("/")
async def root():
    return {"status": "ok", "message": "Bot is running!"}

@app.get("/health")
async def health():
    return {"status": "healthy"}