import httpx
import asyncio
from app.config import settings
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
        # Add natural delay (2-4 seconds)
        await asyncio.sleep(2)

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
            response = await client.post(url, json=payload, headers=headers)
            
            if response.status_code == 200:
                logger.info(f"Message sent to {to_number}")
                return True
            else:
                logger.error(f"Failed to send message: {response.text}")
                return False
                
    except Exception as e:
        logger.error(f"Error sending WhatsApp message: {e}")
        return False