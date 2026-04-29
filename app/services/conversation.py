import json
import logging
from app.core.enums import ConversationState
from app.services.ai_engine import process_message
from app.services.whatsapp import send_message

logger = logging.getLogger(__name__)

async def handle_message(from_number: str, message_text: str):
    """Main function that handles incoming WhatsApp messages"""
    try:
        # For now we use simple in-memory storage
        # In Step 5 we'll connect this to the database
        state = get_conversation_state(from_number)
        history = get_conversation_history(from_number)
        
        # Send to GPT-4o
        ai_response = await process_message(
            message_text=message_text,
            conversation_history=history,
            current_state=state
        )
        
        # Get the reply
        reply = ai_response.get("response", "Sorry, something went wrong.")
        next_state = ai_response.get("next_state", ConversationState.IDLE)
        
        # Update conversation state and history
        update_conversation_state(from_number, next_state)
        update_conversation_history(from_number, message_text, reply)
        
        # Send reply to customer
        await send_message(from_number, reply)
        
        logger.info(f"Handled message from {from_number}, state: {next_state}")
        
    except Exception as e:
        logger.error(f"Error handling message from {from_number}: {e}")
        await send_message(from_number, "Sorry, something went wrong. Please try again.")


# Simple in-memory storage (will be replaced with DB in Step 5)
_conversation_states = {}
_conversation_histories = {}

def get_conversation_state(phone: str) -> str:
    return _conversation_states.get(phone, ConversationState.IDLE)

def update_conversation_state(phone: str, state: str):
    _conversation_states[phone] = state

def get_conversation_history(phone: str) -> list:
    return _conversation_histories.get(phone, [])

def update_conversation_history(phone: str, user_message: str, bot_reply: str):
    if phone not in _conversation_histories:
        _conversation_histories[phone] = []
    
    _conversation_histories[phone].append({"role": "user", "content": user_message})
    _conversation_histories[phone].append({"role": "assistant", "content": bot_reply})
    
    # Keep only last 10 messages to avoid token limits
    if len(_conversation_histories[phone]) > 20:
        _conversation_histories[phone] = _conversation_histories[phone][-20:]