from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_file is for LOCAL runs only; in k8s these come from Secrets via envFrom
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # RabbitMQ — user/pass match the rabbitmq-credentials Secret keys exactly
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_default_user: str
    rabbitmq_default_pass: str
    rabbitmq_queue: str
    
    # Redis -- user/pass match the redis-credentials Secret keys exactly
    redis_host: str
    redis_port: int

    # Postgres -- user/pass match the postgres-credentials Secret keys exactly
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_db: str

    litellm_host: str
    litellm_port: int

    @property
    def litellm_url(self) -> str:
        return f"http://{self.litellm_host}:{self.litellm_port}/v1"

    @property
    def amqp_url(self) -> str:
        return f"amqp://{self.rabbitmq_default_user}:{self.rabbitmq_default_pass}@{self.rabbitmq_host}:{self.rabbitmq_port}/"

    @property
    def amqp_display_url(self) -> str:
        # amqp_url without the credentials - log this one, never amqp_url.
        return f"amqp://{self.rabbitmq_host}:{self.rabbitmq_port}/"

    @property
    def postgres_url(self) -> str:
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def redis_url(self) -> str:
        # no auth on Redis yet; add ":{password}@" before the host if redis gains a password
        return f"redis://{self.redis_host}:{self.redis_port}"


settings = Settings()