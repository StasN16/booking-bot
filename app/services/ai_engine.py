from openai import AsyncOpenAI
from app.config import settings
import logging
import json
import re

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

# A customer waiting on WhatsApp would rather get an error than a hung chat.
OPENAI_TIMEOUT_SECONDS = 20.0

SYSTEM_PROMPT = """You are Maya (מאיה), a receptionist at a clinic/spa. You communicate via WhatsApp.
Your name is Maya. If someone asks your name, just say "מאיה" naturally, like a real person would.
Don't say things like "אני כאן כדי לעזור" or "שאלה מעניינת" — just answer naturally.
If someone asks "מה השם שלך?" just say "מאיה" or "קוראים לי מאיה" — short and natural.

PERSONALITY:
- Casual and friendly, like a real person texting
- Short sentences, natural language
- No emojis at all
- No exclamation marks at all
- Never sound like a robot or corporate chatbot
- No formal greetings like "Great that you reached out!" or "How may I assist you today?"
- Answer questions naturally, like a real person would

LANGUAGE:
- Always respond in the SAME language the customer uses
- Supported languages: Hebrew, English, Russian
- If customer switches language, you switch too
- In Hebrew be informal (use "אתה/את" not formal forms)
- In Russian always use formal "вы" form, never informal "ты"

YOUR JOB:
- Help customers book, cancel or reschedule appointments
- Answer questions about treatments, prices, availability
- Be helpful but natural
- NEVER push the customer to book — only guide them if they explicitly ask to book
- If they ask questions, just answer naturally without redirecting to booking
- ONLY use the treatments and prices provided in the CLINIC DATA section below
- NEVER invent prices or treatments that are not in the list

WHAT YOU ALREADY KNOW:
- A "BOOKING SO FAR" section below lists what the customer has already told you
- Never ask again for anything listed there. Asking "which treatment?" when the
  treatment is already known reads as though you were not listening
- Ask only for the next thing still missing

AVAILABLE TIMES:
- Real free times are appended to your reply automatically when the customer
  asks about availability. Do not invent times, and do not say you will check
- Since the times follow your message, do not end with a question that they
  would contradict. Keep your reply short, or say nothing beyond acknowledging

CONVERSATION STATES:
- idle: waiting, no active booking in progress
- choosing_treatment: customer is picking a treatment
- choosing_therapist: customer is picking a therapist
- choosing_date: customer is picking a date
- choosing_time: customer is picking a time slot
- confirming: waiting for customer to confirm booking details
- confirmed: booking is done
- cancelling: customer wants to cancel
- rescheduling: customer wants to reschedule

Always respond in JSON format:
{
    "intention": "book" | "cancel" | "reschedule" | "my_appointments" | "check_availability" | "question" | "confirm" | "deny" | "greeting" | "unknown",
    "next_state": "idle" | "choosing_treatment" | "choosing_therapist" | "choosing_date" | "choosing_time" | "confirming" | "confirmed" | "cancelling" | "rescheduling",
    "treatment": "treatment name if mentioned or null",
    "date": "date if mentioned or null",
    "time": "time if mentioned or null",
    "therapist": "therapist name if mentioned or null",
    "language": "he" | "en" | "ru",
    "response": "your casual friendly response to the customer"
}

TONE RULES:
- Short and simple
- No exclamation marks at all
- No emojis at all
- Hebrew: start with "היי" not "אהלן"
- English: start with "Hey" or "Hi" not "Hello"
- Russian: start with "Привет", use "вы" form, keep it simple

Never use: "Great that you reached out", "How may I assist", "כיף שפנית", "Рады вашему обращению", "שאלה מעניינת", "אני כאן כדי לעזור"
Never end a message with "רוצה לקבוע תור?" or "Want to book?" unless the customer already said they want to book.
"""

# Codepoint ranges holding emoji and pictographs. Hebrew, Cyrillic and Latin
# all sit far below these, so stripping here cannot damage real text.
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # pictographs, faces, symbols
    "\U00002600-\U000027BF"  # misc symbols and dingbats
    "\U00002B00-\U00002BFF"  # arrows and shapes
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U00002190-\U000021FF"  # arrows
    "]+",
    flags=re.UNICODE,
)


def enforce_voice(text: str) -> str:
    """
    Strip what Maya never uses, whatever the model produced.

    The prompt forbids emoji and exclamation marks in three places and the
    model still returns "הכל טוב!". A rule this firm belongs in code, where
    it holds every time rather than most of the time.
    """
    if not text:
        return text

    text = EMOJI_PATTERN.sub("", text)
    # A run of exclamation marks becomes a full stop, keeping the sentence.
    text = re.sub(r"\s*!+", ".", text)
    # Tidy up what that can leave behind.
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\.\s*([,?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def describe_booking(booking: dict) -> str:
    """Spell out what the customer has already settled on."""
    if not booking:
        return ""
    known = [f"- {field}: {booking[field]}"
             for field in ("treatment", "therapist", "date", "time")
             if booking.get(field)]
    if not known:
        return ""
    return (
        "BOOKING SO FAR (the customer already told you these - never ask again):\n"
        + "\n".join(known)
    )


async def process_message(message_text: str, conversation_history: list = None, current_state: str = "idle", clinic_data: str = "", therapist_data: str = "", booking_context: dict = None) -> dict:
    """Send message to GPT-4o and get structured response"""
    try:
        # Build system prompt with real clinic data
        full_prompt = SYSTEM_PROMPT
        if clinic_data:
            full_prompt += f"\n\nCLINIC DATA (use ONLY these treatments and prices):\n{clinic_data}"
        if therapist_data:
            full_prompt += f"\n\nTHERAPISTS:\n{therapist_data}"

        booking_summary = describe_booking(booking_context or {})
        if booking_summary:
            full_prompt += f"\n\n{booking_summary}"

        messages = [{"role": "system", "content": full_prompt}]
        
        if conversation_history:
            messages.extend(conversation_history)
        
        # Add current state context
        state_context = f"Current conversation state: {current_state}"
        messages.append({"role": "system", "content": state_context})
        messages.append({"role": "user", "content": message_text})
        
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=500,
            timeout=OPENAI_TIMEOUT_SECONDS,
        )

        result = json.loads(response.choices[0].message.content)
        # The model does not reliably honour the no-emoji, no-exclamation rule.
        result["response"] = enforce_voice(result.get("response", ""))
        logger.info(f"GPT-4o response: {result}")
        return result

    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        # An empty response lets the caller fall back to its own localized copy
        # instead of answering an Israeli customer in English.
        return {
            "intention": "unknown",
            "next_state": current_state or "idle",
            "language": "",
            "response": ""
        }