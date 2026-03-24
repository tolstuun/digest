from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://digest:digest@localhost:5432/digest"

    model_config = {"env_file": ".env"}


settings = Settings()
