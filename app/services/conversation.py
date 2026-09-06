import logging
from collections import defaultdict
from datetime import datetime, date
from app.core.enums import ConversationState
from app.services.ai_engine import process_message
from app.services.whatsapp import send_message
from app.services.availability import get_treatments_summary, get_therapists_summary, get_available_slots, get_treatments, find_next_available_date
from app.services.booking import create_appointment, cancel_appointment, get_customer_appointments
from app.services.date_parser import parse_date

HEBREW_DAY_NAMES = {
    0: "יום שני", 1: "יום שלישי", 2: "יום רביעי",
    3: "יום חמישי", 4: "יום שישי", 5: "שבת", 6: "יום ראשון",
}

logger = logging.getLogger(__name__)

async def handle_message(from_number: str, message_text: str):
    """Main function that handles incoming WhatsApp messages"""
    try:
        state = get_conversation_state(from_number)
        history = get_conversation_history(from_number)
        booking_context = get_booking_context(from_number)
        
        # Load real clinic data from database
        clinic_data = await get_treatments_summary()
        therapist_data = await get_therapists_summary()

        # Send to GPT-4o with real clinic data
        ai_response = await process_message(
            message_text=message_text,
            conversation_history=history,
            current_state=state,
            clinic_data=clinic_data,
            therapist_data=therapist_data
        )
        
        reply = ai_response.get("response", "Sorry, something went wrong.")
        next_state = ai_response.get("next_state", ConversationState.IDLE)
        intention = ai_response.get("intention", "unknown")

        # Save booking context as conversation progresses
        if ai_response.get("treatment"):
            booking_context["treatment"] = ai_response.get("treatment")
        if ai_response.get("date"):
            booking_context["date"] = ai_response.get("date")
        if ai_response.get("time"):
            booking_context["time"] = ai_response.get("time")
        if ai_response.get("therapist"):
            booking_context["therapist"] = ai_response.get("therapist")
        
        update_booking_context(from_number, booking_context)

        # Handle specific intentions
        if intention == "check_availability":
            slots_info = await get_real_availability(booking_context)
            if slots_info:
                reply = reply + "\n\n" + slots_info

        elif intention == "confirm" and state == ConversationState.CONFIRMING:
            # Normalize date to ISO format before creating appointment
            booking_date = booking_context.get("date", "")
            parsed = parse_date(booking_date)
            if parsed:
                booking_date = parsed.strftime("%Y-%m-%d")
            else:
                booking_date = date.today().strftime("%Y-%m-%d")

            result = await create_appointment(
                customer_phone=from_number,
                treatment_name=booking_context.get("treatment", ""),
                therapist_name=booking_context.get("therapist", ""),
                appointment_date=booking_date,
                appointment_time=booking_context.get("time", "")
            )

            if result.get("success"):
                reply = f"מעולה, הזמנת תור ל{result['treatment']} עם {result['therapist']} בתאריך {result['date']} בשעה {result['time']}. נתראה"
                next_state = ConversationState.CONFIRMED
                clear_booking_context(from_number)
            elif result.get("error") == "slot_taken":
                reply = result.get("message", "השעה כבר תפוסה. בחר שעה אחרת.")
                next_state = ConversationState.CHOOSING_TIME
            else:
                reply = "משהו השתבש בשמירת התור. נסה שוב"
                next_state = ConversationState.IDLE

        elif intention == "cancel":
            result = await cancel_appointment(customer_phone=from_number)
            if result.get("success"):
                reply = "התור בוטל"
                next_state = ConversationState.IDLE
            else:
                reply = "לא מצאתי תור קרוב לביטול"
                next_state = ConversationState.IDLE

        # Update conversation state and history
        update_conversation_state(from_number, next_state)
        update_conversation_history(from_number, message_text, reply)
        
        # Send reply to customer
        await send_message(from_number, reply)
        
        logger.info(f"Handled message from {from_number}, state: {next_state}")
        
    except Exception as e:
        logger.error(f"Error handling message from {from_number}: {e}")
        await send_message(from_number, "Sorry, something went wrong. Please try again.")


async def get_real_availability(booking_context: dict) -> str:
    """Get real available slots from database"""
    try:
        treatment_name = booking_context.get("treatment", "")
        date_str = booking_context.get("date", "")
        
        if not treatment_name:
            return ""
        
        treatments = await get_treatments()
        treatment = next((t for t in treatments if treatment_name.lower() in t["name"].lower()), None)
        
        if not treatment:
            return ""
        
        target_date = parse_date(date_str)
        if target_date is None:
            target_date = date.today()

        if target_date < date.today():
            return "לא ניתן להזמין תור לתאריך שעבר"

        slots = await get_available_slots(treatment["id"], target_date)

        if not slots:
            next_date = await find_next_available_date(treatment["id"], target_date)
            if next_date:
                day_name = HEBREW_DAY_NAMES.get(next_date.weekday(), "")
                return f"אין זמינות ביום זה. היום הפנוי הקרוב הוא {day_name} {next_date.strftime('%d/%m')}"
            return "אין זמינות בימים הקרובים"

        grouped = defaultdict(list)
        for s in slots:
            grouped[s["therapist_name"]].append(s["time"])

        lines = []
        for name, times in grouped.items():
            lines.append(f"{name}: {', '.join(times[:6])}")

        return "זמנים פנויים:\n" + "\n".join(lines)
        
    except Exception as e:
        logger.error(f"Error getting availability: {e}")
        return ""


# In-memory storage
_conversation_states = {}
_conversation_histories = {}
_booking_contexts = {}

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
    
    if len(_conversation_histories[phone]) > 20:
        _conversation_histories[phone] = _conversation_histories[phone][-20:]

def get_booking_context(phone: str) -> dict:
    return _booking_contexts.get(phone, {})

def update_booking_context(phone: str, context: dict):
    _booking_contexts[phone] = context

def clear_booking_context(phone: str):
    _booking_contexts[phone] = {}