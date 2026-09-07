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
    # The server checks its own trail on this interval and logs what the
    # analyzers find, so a broken conversation surfaces without anyone
    # remembering to look. 0 turns the checking off.
    AUDIT_WATCH_MINUTES: int = 15
    # Informational findings are for reading in a report, not for
    # interrupting someone watching the server.
    AUDIT_WATCH_INFO: bool = False
    OPENAI_API_KEY: str = ""
    WHATSAPP_TOKEN: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_APP_SECRET: str = ""
    WHATSAPP_PHONE_ID: str = ""
    REDIS_URL: str = "redis://localhost:6379"
    # Dashboard sign-in. JWT_SECRET signs the tokens; leaving either of
    # these blank keeps the API shut rather than open.
    JWT_SECRET: str = ""
    ADMIN_PASSWORD: str = ""
    JWT_HOURS: int = 12
    SENTRY_DSN: str = ""

    class Config:
        env_file = ".env"

settings = Settings()