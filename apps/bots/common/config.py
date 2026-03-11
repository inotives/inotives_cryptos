from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="configs/envs/.env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str

    @property
    def db_dsn(self) -> str:
        return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    # Exchange API keys
    binance_api_key: str = ""
    binance_api_secret: str = ""

    cryptocom_api_key: str = ""
    cryptocom_api_secret: str = ""


settings = Settings()
