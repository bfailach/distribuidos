from datetime import datetime

from pydantic import BaseModel


class RegistroCreate(BaseModel):
    contenido: str


class RegistroOut(BaseModel):
    id: str
    contenido: str
    origen_servidor: str
    timestamp: datetime

    class Config:
        from_attributes = True
