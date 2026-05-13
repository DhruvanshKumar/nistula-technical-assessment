"""
Rule-based query classifier using keyword matching.

This avoids a round-trip to Claude just for classification and keeps
latency low. For production, a fine-tuned embedding classifier or a
lightweight LLM call could replace this.
"""

import re

# Ordered by specificity — first match wins
_RULES = [
    (
        "complaint",
        r"\b(not working|broken|unhappy|disappointed|unacceptable|complaint|issue|problem|dirty|disgusting|refund|terrible|awful|horrible)\b",
    ),
    (
        "post_sales_checkin",
        r"\b(check.?in|check.?out|wifi|wi-fi|password|key|access|arrival|early check|late check|checkout time)\b",
    ),
    (
        "special_request",
        r"\b(early check-?in|late check-?out|airport transfer|pickup|drop|extra bed|cot|birthday|anniversary|surprise|vegetarian|allergy|dietary)\b",
    ),
    (
        "pre_sales_availability",
        r"\b(available|availability|free|vacant|book|dates?|from .* to|april|may|june|july|august|september|october|november|december|january|february|march)\b",
    ),
    (
        "pre_sales_pricing",
        r"\b(rate|price|cost|per night|how much|charge|fee|tariff|adult|guest|person|people)\b",
    ),
    (
        "general_enquiry",
        r".*",  # catch-all
    ),
]


def classify_query(message: str) -> str:
    """Return the query type for the given message text."""
    lowered = message.lower()

    # Special_request keywords overlap with post_sales — check complaint first
    # then do a two-pass for special_request vs post_sales
    for query_type, pattern in _RULES:
        if re.search(pattern, lowered):
            return query_type

    return "general_enquiry"
