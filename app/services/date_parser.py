import re
from datetime import date, timedelta

HEBREW_DAYS = {
    "יום ראשון": 6,
    "יום שני": 0,
    "יום שלישי": 1,
    "יום רביעי": 2,
    "יום חמישי": 3,
    "יום שישי": 4,
    "יום שבת": 5,
    "ראשון": 6,
    "שלישי": 1,
    "רביעי": 2,
    "חמישי": 3,
    "שישי": 4,
    "שבת": 5,
    "שני": 0,
}

ENGLISH_DAYS = {
    "sunday": 6,
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
}


def parse_date(date_str: str) -> date | None:
    if not date_str:
        return None

    today = date.today()
    text = date_str.strip()

    if "היום" in text or text.lower() == "today":
        return today

    if "מחר" in text or text.lower() == "tomorrow":
        return today + timedelta(days=1)

    iso_match = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if iso_match:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            return None

    slash_match = re.match(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", text)
    if slash_match:
        day = int(slash_match.group(1))
        month = int(slash_match.group(2))
        year_str = slash_match.group(3)
        if year_str:
            year = int(year_str)
            if year < 100:
                year += 2000
        else:
            year = today.year
        try:
            result = date(year, month, day)
            if result < today and not year_str:
                result = date(year + 1, month, day)
            return result
        except ValueError:
            return None

    for hebrew_name, weekday in HEBREW_DAYS.items():
        if hebrew_name in text:
            return _next_weekday(today, weekday)

    for eng_name, weekday in ENGLISH_DAYS.items():
        if eng_name in text.lower():
            return _next_weekday(today, weekday)

    return None


def _next_weekday(from_date: date, target_weekday: int) -> date:
    days_ahead = target_weekday - from_date.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return from_date + timedelta(days=days_ahead)


def parse_time(time_str: str) -> str | None:
    """
    Normalize a time string to canonical HH:MM, or None if unparseable.

    GPT returns times in whatever shape the customer typed, so accept
    "14:00", "14.00", "2pm", "2 PM", "9:30" and ranges like "14:00-15:00"
    (the start of the range wins).
    """
    if not time_str:
        return None

    text = time_str.strip().lower()

    # A range means the customer named a start and an end - keep the start.
    text = re.split(r"[-–—]|\bto\b|\bעד\b", text)[0].strip()

    match = re.search(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?", text)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    meridiem = match.group(3)

    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    return f"{hour:02d}:{minute:02d}"
