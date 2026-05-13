import uuid
from app.models import InboundMessage
def normalise_message(payload: InboundMessage, query_type: str) -> dict:
    """
    Convert a raw InboundMessage into the unified schema dict.
    Adds a generated UUID as message_id.
    """
    return {
        "message_id": str(uuid.uuid4()),
        "source": payload.source,
        "guest_name": payload.guest_name,
        "message_text": payload.message,
        "timestamp": payload.timestamp.isoformat(),
        "booking_ref": payload.booking_ref,
        "property_id": payload.property_id,
        "query_type": query_type,
    }
