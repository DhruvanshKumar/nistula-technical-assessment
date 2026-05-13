import os
import json
import httpx
import logging
from typing import Tuple
logger = logging.getLogger(__name__)
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"
PROPERTY_CONTEXT = """
Property: Villa B1, Assagao, North Goa
Bedrooms: 3 | Max guests: 6 | Private pool: Yes
Check-in: 2pm | Check-out: 11am
Base rate: INR 18,000 per night (up to 4 guests)
Extra guest: INR 2,000 per night per person
WiFi password: Nistula@2024
Caretaker: Available 8am to 10pm
Chef on call: Yes, pre-booking required
Availability April 20-24: Available
Cancellation: Free cancellation up to 7 days before check-in
""".strip()
# Local heuristic weights per query type
LOCAL_SCORES = {
    "post_sales_checkin": 1.0,
    "pre_sales_availability": 0.9,
    "pre_sales_pricing": 0.9,
    "general_enquiry": 0.75,
    "special_request": 0.75,
    "complaint": 0.40,
}
SYSTEM_PROMPT = f"""
You are a warm, professional guest relations assistant for Nistula, a luxury villa hospitality brand in Goa, India.
PROPERTY CONTEXT:
{PROPERTY_CONTEXT}
Your job is to draft a helpful, friendly reply to a guest message.
RULES:
1. Always address the guest by their first name.
2. Keep replies concise but complete — answer every question asked.
3. If a question cannot be answered from the property context, say you will confirm shortly rather than guessing.
4. Never invent facts (rates, dates, amenities) not listed in the property context.
5. Match the tone of the source channel: WhatsApp = casual and warm; Booking.com / Airbnb = slightly more formal.
6. For complaints, acknowledge the issue empathetically without admitting liability; promise follow-up.
RESPONSE FORMAT — you must return ONLY valid JSON, no markdown, no extra text:
{{
  "reply": "<guest-facing message>",
  "confidence": <float between 0.0 and 1.0>
}}
Set confidence based on:
- 1.0 if you can answer every part of the message definitively from the context above
- 0.7-0.9 if you can answer most parts but are making minor assumptions
- 0.4-0.6 if significant parts of the question need external confirmation
- 0.0-0.4 if this is a complaint or you cannot answer without more info
""".strip()

def _build_user_prompt(msg: dict) -> str:
    channel_label = {
        "whatsapp": "WhatsApp",
        "booking_com": "Booking.com",
        "airbnb": "Airbnb",
        "instagram": "Instagram DM",
        "direct": "Direct (website/email)",
    }.get(msg["source"], msg["source"])

    ref_line = f"Booking reference: {msg['booking_ref']}" if msg.get("booking_ref") else "No booking reference provided."
    prop_line = f"Property: {msg['property_id']}" if msg.get("property_id") else ""

    return f"""Channel: {channel_label}
Guest name: {msg['guest_name']}
{ref_line}
{prop_line}
Query type: {msg['query_type']}
Message: {msg['message_text']}

Draft a reply and return the JSON object."""


async def get_claude_reply(normalised: dict) -> Tuple[str, float]:
    """
    Call the Claude API and return (drafted_reply, confidence_score).
    Falls back gracefully on API or parse errors.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": MODEL,
        "max_tokens": 1000,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": _build_user_prompt(normalised)}
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(ANTHROPIC_API_URL, headers=headers, json=body)
            response.raise_for_status()
        data = response.json()
        raw_text = data["content"][0]["text"].strip()
        logger.debug(f"Claude raw response: {raw_text}")
        parsed = json.loads(raw_text)
        reply = parsed.get("reply", "")
        claude_confidence = float(parsed.get("confidence", 0.5))
        claude_confidence = max(0.0, min(1.0, claude_confidence))  # clamp
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude JSON response: {e} | raw: {raw_text}")
        reply = raw_text  # use raw text as fallback reply
        claude_confidence = 0.5
    except httpx.HTTPStatusError as e:
        logger.error(f"Claude API HTTP error: {e.response.status_code} — {e.response.text}")
        raise HTTPException(status_code=502, detail=f"Claude API error: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Unexpected error calling Claude: {e}", exc_info=True)
        raise
    local_score = LOCAL_SCORES.get(normalised["query_type"], 0.7)
    final_confidence = 0.7 * claude_confidence + 0.3 * local_score
    if normalised["query_type"] == "complaint":
        final_confidence = min(final_confidence, 0.55)
    return reply, final_confidence
from fastapi import HTTPException
