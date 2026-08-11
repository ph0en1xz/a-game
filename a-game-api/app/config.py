from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_file is for LOCAL runs only; in k8s these come from Secrets via envFrom
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Redis -- user/pass match the redis-credentials Secret keys exactly
    redis_host: str
    redis_port: int

    postgres_user: str
    postgres_password: str
    postgres_host: str
    postgres_port: int
    postgres_db: str

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}"

    @property
    def postgres_url(self) -> str:
        credentials = f"{self.postgres_user}:{self.postgres_password}"
        return f"postgresql://{credentials}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

settings = Settings()