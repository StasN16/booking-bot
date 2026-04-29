from enum import Enum

class ConversationState(str, Enum):
    IDLE = "idle"
    CHOOSING_TREATMENT = "choosing_treatment"
    CHOOSING_THERAPIST = "choosing_therapist"
    CHOOSING_DATE = "choosing_date"
    CHOOSING_TIME = "choosing_time"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    CANCELLING = "cancelling"
    RESCHEDULING = "rescheduling"