from pydantic import BaseModel
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class Coordinates(BaseModel):
    latitude: float
    longitude: float

class Orders(BaseModel):
    customer_id: int
    coordinates: Coordinates
    model_config = ConfigDict(extra='allow')
    order_volume: float
    strength: str
    Dmax: float
    consistency: str
    exposure: str
    date: datetime



