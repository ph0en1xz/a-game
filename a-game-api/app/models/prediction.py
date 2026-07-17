from pydantic import BaseModel


class Prediction(BaseModel):
    description:str
    match:str