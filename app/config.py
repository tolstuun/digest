from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://digest:digest@localhost:5432/digest"
    anthropic_api_key: str = ""
    extraction_model: str = "claude-haiku-4-5-20251001"

    model_config = {"env_file": ".env"}


settings = Settings()
