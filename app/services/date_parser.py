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
