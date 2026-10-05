from typing import Literal, Optional, List
from pydantic import BaseModel


EventType = Literal[
    "ORDER",
    "SUBSTITUTE",
    "CANCEL",
    "DELIVERED",
    "PAYMENT",
    "PROMISE",
    "ADJUST_CREDIT",
    "DISPUTE",
]


class RawItem(BaseModel):
    item: str
    qty: Optional[float] = None
    unit: Optional[str] = None
    needs_clarification: bool = False
    reason: Optional[str] = None


class RawEvent(BaseModel):
    type: EventType
    date: str

    # ORDER
    items: Optional[List[RawItem]] = None

    # SUBSTITUTE
    from_item: Optional[str] = None
    to_item: Optional[str] = None

    # PAYMENT / PROMISE / ADJUST_CREDIT
    amount: Optional[float] = None

    # DISPUTE
    note: Optional[str] = None


class ExtractionResult(BaseModel):
    events: List[RawEvent]