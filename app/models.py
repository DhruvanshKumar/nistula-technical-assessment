from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime


SourceType = Literal["whatsapp", "booking_com", "airbnb", "instagram", "direct"]

QueryType = Literal[
    "pre_sales_availability",
    "pre_sales_pricing",
    "post_sales_checkin",
    "special_request",
    "complaint",
    "general_enquiry",
]

ActionType = Literal["auto_send", "agent_review", "escalate"]


class InboundMessage(BaseModel):
    source: SourceType
    guest_name: str
    message: str
    timestamp: datetime
    booking_ref: Optional[str] = None
    property_id: Optional[str] = None


class WebhookResponse(BaseModel):
    message_id: str
    query_type: QueryType
    drafted_reply: str
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    action: ActionType
