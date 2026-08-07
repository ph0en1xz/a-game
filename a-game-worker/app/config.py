from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    rabbitmq_host:str
    rabbitmq_port:int
    rabbitmq_default_user:str
    rabbitmq_default_pass:str
    rabbitmq_queue:str

    postgres_host:str
    postgres_port:int
    postgres_user:str
    postgres_password:str
    postgres_db:str

    sports_api_key:str
    sports_api_url:str
    sports_competitions_matches_endpoint:str
    sports_competitions_endpoint:str
    sports_historic_matches_endpoint:str

    @property
    def historic_matches_endpoint(self) -> str:
        return f"{self.sports_api_url}/{self.sports_historic_matches_endpoint}"

    @property
    def competitions_endpoint(self) -> str:
        return f"{self.sports_api_url}/{self.sports_competitions_endpoint}"
    
    @property
    def competitions_matches_endpoint(self) -> str:
        return f"{self.sports_api_url}/{self.sports_competitions_matches_endpoint}"
    
    @property
    def amqp_url(self) -> str:
        return f"amqp://{self.rabbitmq_default_user}:{self.rabbitmq_default_pass}@{self.rabbitmq_host}:{self.rabbitmq_port}/"
    
    @property
    def postgres_url(self) -> str:
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
    

settings = Settings()