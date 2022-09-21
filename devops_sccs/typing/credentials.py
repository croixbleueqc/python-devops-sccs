from pydantic import BaseModel


class Credentials(BaseModel):
    user: str
    author: str
    apikey: str
