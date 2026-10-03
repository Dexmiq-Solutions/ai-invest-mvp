import os

from dotenv import load_dotenv


load_dotenv()


class Settings:
    APP_ENV: str = os.getenv("APP_ENV", "development")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    POSTGRES_URL: str = os.getenv("POSTGRES_URL", "")


settings = Settings()