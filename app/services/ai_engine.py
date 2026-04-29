from openai import AsyncOpenAI
from app.config import settings
import logging

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

SYSTEM_PROMPT = """You are a helpful booking assistant for a clinic/spa business.
Your job is to help customers book, cancel, or modify appointments.

When a customer sends a message, extract their intention and respond in JSON format:
{
    "intention": "book" | "cancel" | "reschedule" | "check_availability" | "greeting" | "unknown",
    "treatment": "name of treatment if mentioned or null",
    "date": "date if mentioned or null", 
    "time": "time if mentioned or null",
    "therapist": "therapist name if mentioned or null",
    "response": "your friendly response to the customer in their language"
}

Always respond in the same language the customer uses.
Be friendly, professional and concise.
"""

async def process_message(message_text: str, conversation_history: list = None) -> dict:
    """Send message to GPT-4o and get structured response"""
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        
        if conversation_history:
            messages.extend(conversation_history)
        
        messages.append({"role": "user", "content": message_text})
        
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=500
        )
        
        import json
        result = json.loads(response.choices[0].message.content)
        logger.info(f"GPT-4o response: {result}")
        return result
        
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return {
            "intention": "unknown",
            "response": "Sorry, I'm having trouble understanding. Please try again."
        }