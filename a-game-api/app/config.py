from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_file is for LOCAL runs only; in k8s these come from Secrets via envFrom
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Redis -- user/pass match the redis-credentials Secret keys exactly
    redis_host: str
    redis_port: int

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}"
