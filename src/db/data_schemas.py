from pydantic import BaseModel, Field
from typing import Optional

class Event(BaseModel):
    eventName: str
    registrationLink: str
    startDate: str
    endDate: Optional[str] = None
    location: Optional[str] = None
    platform: Optional[str] = None
    days_remaining: Optional[int] = None
    prizePool: Optional[str] = None
    cost: Optional[str] = None
    eligibility: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    score: Optional[int] = None
    eventType: Optional[str] = None
