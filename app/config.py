from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = ""
    # Every query is scoped to this business. Keep the seed data and the
    # running bot pointed at the same id, or the bot sees an empty clinic.
    BUSINESS_ID: str = "550e8400-e29b-41d4-a716-446655440000"
    # Appointment times are interpreted in this zone, so the bot keeps saying
    # the right local time even when the server runs on UTC.
    TIMEZONE: str = "Asia/Jerusalem"
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