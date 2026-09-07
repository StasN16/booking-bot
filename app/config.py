from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = ""
    # Every query is scoped to this business. Keep the seed data and the
    # running bot pointed at the same id, or the bot sees an empty clinic.
    BUSINESS_ID: str = "550e8400-e29b-41d4-a716-446655440000"
    # Appointment times are interpreted in this zone, so the bot keeps saying
    # the right local time even when the server runs on UTC.
    TIMEZONE: str = "Asia/Jerusalem"

    # Reminders. The in-process loop is enough for a single server; set
    # REMINDERS_ENABLED=false and drive /api/v1/tasks/reminders from an
    # external scheduler instead when running more than one instance.
    REMINDERS_ENABLED: bool = True
    REMINDER_POLL_MINUTES: int = 10
    # Shared secret for the reminder endpoint. Blank leaves it closed.
    TASKS_TOKEN: str = ""

    # Pause before replying, so the bot does not answer machine-fast.
    # Measured against real traffic this was 2 seconds of a 5.4 second wait,
    # the largest part anyone here controls, so it is turned down. Half a
    # second still reads as a person typing rather than a machine answering.
    REPLY_DELAY_SECONDS: float = 0.5

    # Audit trail. One JSON object per operation, written to this file.
    AUDIT_ENABLED: bool = True
    AUDIT_LOG_PATH: str = "logs/audit.jsonl"
    # Anything slower than this is reported by the analysis as worth a look.
    AUDIT_SLOW_MS: int = 5000
    OPENAI_API_KEY: str = ""
    WHATSAPP_TOKEN: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_APP_SECRET: str = ""
    WHATSAPP_PHONE_ID: str = ""
    REDIS_URL: str = "redis://localhost:6379"
    JWT_SECRET: str = ""
    SENTRY_DSN: str = ""

    class Config:
        env_file = ".env"

settings = Settings()