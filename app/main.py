from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from app.models import InboundMessage, WebhookResponse
from app.classifier import classify_query
from app.claude_handler import get_claude_reply
from app.schema import normalise_message
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Nistula Guest Message Handler",
    description="Unified webhook endpoint for guest messages across all channels",
    version="1.0.0"
)


@app.post("/webhook/message", response_model=WebhookResponse)
async def handle_message(payload: InboundMessage):
    """
    Receives an inbound guest message, normalises it, classifies it,
    sends it to Claude for a drafted reply, and returns the result.
    """
    logger.info(f"Received message from {payload.source} — guest: {payload.guest_name}")

    # Step 1: Classify query type
    query_type = classify_query(payload.message)
    logger.info(f"Classified as: {query_type}")

    # Step 2: Normalise into unified schema
    normalised = normalise_message(payload, query_type)
    logger.info(f"Normalised message_id: {normalised['message_id']}")

    # Step 3: Get Claude-drafted reply + confidence score
    drafted_reply, confidence_score = await get_claude_reply(normalised)

    # Step 4: Determine action based on confidence + query type
    if query_type == "complaint" or confidence_score < 0.60:
        action = "escalate"
    elif confidence_score >= 0.85:
        action = "auto_send"
    else:
        action = "agent_review"

    logger.info(f"Confidence: {confidence_score:.2f} → Action: {action}")

    return WebhookResponse(
        message_id=normalised["message_id"],
        query_type=query_type,
        drafted_reply=drafted_reply,
        confidence_score=round(confidence_score, 2),
        action=action,
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
