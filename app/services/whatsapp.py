import httpx
import asyncio
from app.config import settings
from app.core import audit
import logging

logger = logging.getLogger(__name__)

WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

async def send_typing_indicator(to_number: str):
    """Send 'typing...' indicator to customer"""
    try:
        url = f"{WHATSAPP_API_URL}/{settings.WHATSAPP_PHONE_ID}/messages"
        headers = {
            "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_number,
            "type": "reaction",
            "reaction": {"message_id": "", "emoji": ""}
        }
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, headers=headers)
    except Exception:
        pass  # Typing indicator is optional, don't fail if it doesn't work

async def send_message(to_number: str, message: str) -> bool:
    """Send a WhatsApp message to a customer"""
    try:
        # A pause so replies do not feel machine-fast. It is pure latency,
        # so it is configurable rather than baked in.
        if settings.REPLY_DELAY_SECONDS > 0:
            await asyncio.sleep(settings.REPLY_DELAY_SECONDS)

        url = f"{WHATSAPP_API_URL}/{settings.WHATSAPP_PHONE_ID}/messages"
        
        headers = {
            "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_number,
            "type": "text",
            "text": {"body": message}
        }
        
        async with httpx.AsyncClient() as client:
            # Timed, so the report can separate the network call from the
            # deliberate pause above it.
            with audit.step("whatsapp.api_call", inputs={
                "to": audit.mask_phone(to_number),
                "body_chars": len(message),
            }) as api:
                response = await client.post(url, json=payload, headers=headers)
                api.outputs = {"status_code": response.status_code}
                if response.status_code != 200:
                    api.outputs["body"] = response.text

            if response.status_code == 200:
                logger.info(f"Message sent to {to_number}")
                return True
            else:
                logger.error(f"Failed to send message: {response.text}")
                return False
                
    except Exception as e:
        logger.error(f"Error sending WhatsApp message: {e}")
        audit.record("whatsapp.api_call",
                     inputs={"to": audit.mask_phone(to_number)},
                     error=f"{type(e).__name__}: {e}")
        return False