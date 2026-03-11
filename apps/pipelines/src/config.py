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

    # External API keys
    coingecko_api_key:      str = ""
    coingecko_api_key_type: str = "demo"   # "demo" or "pro"
    coinmarketcap_api_key:  str = ""


settings = Settings()
