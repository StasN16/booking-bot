from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = ""
    OPENAI_API_KEY: str = ""
    WHATSAPP_TOKEN: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_APP_SECRET: str = ""
    REDIS_URL: str = "redis://localhost:6379"
    JWT_SECRET: str = ""
    SENTRY_DSN: str = ""

    class Config:
        env_file = ".env"

settings = Settings()