import logging
from collections import defaultdict
from datetime import datetime, date
from app.core.enums import ConversationState
from app.services.ai_engine import process_message
from app.services.whatsapp import send_message
from app.services.availability import (
    get_treatments_summary,
    get_therapists_summary,
    get_available_slots,
    get_treatments,
    find_next_available_date,
)
from app.services.booking import (
    create_appointment,
    cancel_appointment,
    reschedule_appointment,
    get_customer_appointments,
)
from app.services.date_parser import parse_date

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "he"

DAY_NAMES = {
    "he": ["יום שני", "יום שלישי", "יום רביעי", "יום חמישי", "יום שישי", "שבת", "יום ראשון"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "ru": ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"],
}

# Customer-facing copy. Booking services return error codes rather than text so
# that every reply can be rendered in the language the customer is writing in.
MESSAGES = {
    "he": {
        "booked": "סגור, קבעתי לך {treatment} עם {therapist} ב{date} בשעה {time}. נתראה",
        "rescheduled": "העברתי את התור ל{date} בשעה {time}",
        "cancelled": "ביטלתי את התור ל{treatment} ב{date} בשעה {time}",
        "slot_taken": "השעה הזאת כבר נתפסה. רוצה לבחור שעה אחרת",
        "no_appointment": "לא מצאתי לך תור קרוב",
        "bad_datetime": "לא הצלחתי להבין את התאריך או השעה. אפשר שוב",
        "in_the_past": "הזמן הזה כבר עבר. אפשר לבחור מועד אחר",
        "treatment_not_found": "לא מצאתי את הטיפול הזה אצלנו",
        "booking_failed": "משהו השתבש בשמירת התור. אפשר לנסות שוב",
        "past_date": "התאריך הזה כבר עבר",
        "no_slots_next": "אין זמינות ביום הזה. הכי קרוב שיש זה {day} {date}",
        "no_slots_week": "אין זמינות בימים הקרובים",
        "free_times": "זמנים פנויים:",
        "your_appointments": "התורים שלך:",
        "no_appointments": "אין לך תורים קרובים",
        "error": "משהו השתבש. אפשר לנסות שוב",
    },
    "en": {
        "booked": "Done, booked you for {treatment} with {therapist} on {date} at {time}. See you",
        "rescheduled": "Moved your appointment to {date} at {time}",
        "cancelled": "Cancelled your {treatment} on {date} at {time}",
        "slot_taken": "That time just got taken. Want to pick another one",
        "no_appointment": "I couldn't find an upcoming appointment for you",
        "bad_datetime": "I couldn't make out the date or time. Mind saying it again",
        "in_the_past": "That time has already passed. Pick another one",
        "treatment_not_found": "I couldn't find that treatment here",
        "booking_failed": "Something went wrong saving the appointment. Try again",
        "past_date": "That date has already passed",
        "no_slots_next": "Nothing free that day. Closest is {day} {date}",
        "no_slots_week": "Nothing free in the next few days",
        "free_times": "Open times:",
        "your_appointments": "Your appointments:",
        "no_appointments": "You don't have any upcoming appointments",
        "error": "Something went wrong. Try again",
    },
    "ru": {
        "booked": "Готово, записала вас на {treatment} к {therapist} {date} в {time}. До встречи",
        "rescheduled": "Перенесла вашу запись на {date} в {time}",
        "cancelled": "Отменила вашу запись на {treatment} {date} в {time}",
        "slot_taken": "Это время уже заняли. Хотите выбрать другое",
        "no_appointment": "Я не нашла у вас ближайшей записи",
        "bad_datetime": "Не разобрала дату или время. Повторите, пожалуйста",
        "in_the_past": "Это время уже прошло. Выберите другое",
        "treatment_not_found": "Я не нашла у нас такую процедуру",
        "booking_failed": "Что-то пошло не так при сохранении записи. Попробуйте ещё раз",
        "past_date": "Эта дата уже прошла",
        "no_slots_next": "В этот день свободного времени нет. Ближайшее — {day} {date}",
        "no_slots_week": "В ближайшие дни свободного времени нет",
        "free_times": "Свободное время:",
        "your_appointments": "Ваши записи:",
        "no_appointments": "У вас нет ближайших записей",
        "error": "Что-то пошло не так. Попробуйте ещё раз",
    },
}


def t(language: str, key: str, **kwargs) -> str:
    """Look up customer-facing copy, falling back to the default language."""
    catalog = MESSAGES.get(language) or MESSAGES[DEFAULT_LANGUAGE]
    template = catalog.get(key) or MESSAGES[DEFAULT_LANGUAGE].get(key, "")
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return template


def day_name(target: date, language: str) -> str:
    names = DAY_NAMES.get(language) or DAY_NAMES[DEFAULT_LANGUAGE]
    return names[target.weekday()]


async def handle_message(from_number: str, message_text: str):
    """Main function that handles incoming WhatsApp messages"""
    language = DEFAULT_LANGUAGE
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

        reply = ai_response.get("response", "")
        next_state = ai_response.get("next_state", ConversationState.IDLE)
        intention = ai_response.get("intention", "unknown")
        language = ai_response.get("language") or DEFAULT_LANGUAGE

        # Save booking context as conversation progresses
        for field in ("treatment", "date", "time", "therapist"):
            if ai_response.get(field):
                booking_context[field] = ai_response.get(field)

        update_booking_context(from_number, booking_context)

        if intention == "check_availability":
            slots_info = await get_real_availability(booking_context, language)
            if slots_info:
                reply = (reply + "\n\n" + slots_info).strip()

        elif intention == "confirm" and state == ConversationState.CONFIRMING:
            result = await create_appointment(
                customer_phone=from_number,
                treatment_name=booking_context.get("treatment", ""),
                therapist_name=booking_context.get("therapist", ""),
                appointment_date=booking_context.get("date", ""),
                appointment_time=booking_context.get("time", "")
            )

            if result.get("success"):
                reply = t(language, "booked", **result)
                next_state = ConversationState.CONFIRMED
                clear_booking_context(from_number)
            else:
                reply, next_state = booking_failure(language, result, ConversationState.CHOOSING_TIME)

        elif intention == "reschedule":
            # Only act once the customer has actually named a new slot.
            if booking_context.get("date") and booking_context.get("time"):
                result = await reschedule_appointment(
                    customer_phone=from_number,
                    new_date=booking_context.get("date", ""),
                    new_time=booking_context.get("time", ""),
                )
                if result.get("success"):
                    reply = t(language, "rescheduled", **result)
                    next_state = ConversationState.CONFIRMED
                    clear_booking_context(from_number)
                else:
                    reply, next_state = booking_failure(language, result, ConversationState.RESCHEDULING)
            else:
                next_state = ConversationState.RESCHEDULING

        elif intention == "cancel":
            result = await cancel_appointment(customer_phone=from_number)
            if result.get("success"):
                reply = t(language, "cancelled", **result)
                clear_booking_context(from_number)
            else:
                reply = t(language, "no_appointment")
            next_state = ConversationState.IDLE

        elif intention == "my_appointments":
            appointments = await get_customer_appointments(from_number)
            if appointments:
                lines = [
                    f"- {a['treatment']} {a['date']} {a['time']} ({a['therapist']})"
                    for a in appointments
                ]
                reply = t(language, "your_appointments") + "\n" + "\n".join(lines)
            else:
                reply = t(language, "no_appointments")
            next_state = ConversationState.IDLE

        if not reply:
            reply = t(language, "error")

        # Update conversation state and history
        update_conversation_state(from_number, next_state)
        update_conversation_history(from_number, message_text, reply)

        # Send reply to customer
        await send_message(from_number, reply)

        logger.info(f"Handled message from {from_number}, state: {next_state}")

    except Exception as e:
        logger.exception(f"Error handling message from {from_number}: {e}")
        await send_message(from_number, t(language, "error"))


def booking_failure(language: str, result: dict, retry_state: str):
    """Turn a booking service error code into a reply and the state to fall back to."""
    error = result.get("error", "")
    if error == "slot_taken":
        return t(language, "slot_taken"), retry_state
    if error == "no_appointment":
        return t(language, "no_appointment"), ConversationState.IDLE
    if error in ("bad_datetime", "in_the_past"):
        return t(language, error), retry_state
    if error == "treatment_not_found":
        return t(language, "treatment_not_found"), ConversationState.CHOOSING_TREATMENT
    return t(language, "booking_failed"), ConversationState.IDLE


async def get_real_availability(booking_context: dict, language: str = DEFAULT_LANGUAGE) -> str:
    """Get real available slots from database"""
    try:
        treatment_name = booking_context.get("treatment", "")
        date_str = booking_context.get("date", "")

        if not treatment_name:
            return ""

        treatments = await get_treatments()
        treatment = next(
            (t_ for t_ in treatments if treatment_name.lower() in t_["name"].lower()),
            None
        )

        if not treatment:
            return ""

        target_date = parse_date(date_str)
        if target_date is None:
            target_date = date.today()

        if target_date < date.today():
            return t(language, "past_date")

        slots = await get_available_slots(treatment["id"], target_date)

        if not slots:
            next_date = await find_next_available_date(treatment["id"], target_date)
            if next_date:
                return t(
                    language,
                    "no_slots_next",
                    day=day_name(next_date, language),
                    date=next_date.strftime("%d/%m"),
                )
            return t(language, "no_slots_week")

        grouped = defaultdict(list)
        for slot in slots:
            grouped[slot["therapist_name"]].append(slot["time"])

        lines = [
            f"{name}: {', '.join(times[:6])}"
            for name, times in grouped.items()
        ]

        return t(language, "free_times") + "\n" + "\n".join(lines)

    except Exception as e:
        logger.exception(f"Error getting availability: {e}")
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
