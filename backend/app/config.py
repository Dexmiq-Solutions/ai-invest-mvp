import os

from dotenv import load_dotenv


load_dotenv()


class Settings:
    APP_ENV: str = os.getenv("APP_ENV", "development")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.getenv("LOG_FILE", "ai_investigator.log")
    LOG_TO_FILE: bool = os.getenv("LOG_TO_FILE", "false").lower() in ("true", "1", "yes")
    POSTGRES_URL: str = os.getenv("POSTGRES_URL", "")

    # LangSmith Tracing
    LANGCHAIN_TRACING_V2: bool = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() in ("true", "1", "yes")
    LANGCHAIN_API_KEY: str = os.getenv("LANGCHAIN_API_KEY", "")
    LANGCHAIN_PROJECT: str = os.getenv("LANGCHAIN_PROJECT", "ai-software-investigation")


settings = Settings()