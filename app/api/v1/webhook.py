from fastapi import APIRouter, Request, HTTPException, Query
from app.config import settings
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """Meta calls this to verify your webhook URL is real"""
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")

@router.post("/webhook")
async def receive_message(request: Request):
    """Meta calls this when a WhatsApp message is received"""
    body = await request.json()
    logger.info(f"Received webhook: {body}")
    
    try:
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if messages:
            message = messages[0]
            from_number = message.get("from")
            message_text = message.get("text", {}).get("body", "")
            logger.info(f"Message from {from_number}: {message_text}")
        
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return {"status": "ok"}