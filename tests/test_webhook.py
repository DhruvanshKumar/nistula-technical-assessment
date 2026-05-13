"""
Tests for the Nistula guest message handler.
Run with: pytest tests/ -v
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.main import app
from app.classifier import classify_query
from app.schema import normalise_message
from app.models import InboundMessage
from datetime import datetime

client = TestClient(app)

# ─────────────────────────────────────────────────────────
# Classifier unit tests
# ─────────────────────────────────────────────────────────

def test_classify_availability():
    assert classify_query("Is the villa available from April 20 to 24?") == "pre_sales_availability"

def test_classify_pricing():
    assert classify_query("What is the rate for 2 adults 3 nights?") == "pre_sales_pricing"

def test_classify_checkin():
    assert classify_query("What time is check-in? And what is the WiFi password?") == "post_sales_checkin"

def test_classify_complaint():
    assert classify_query("The AC is not working. I am not happy.") == "complaint"

def test_classify_special_request():
    assert classify_query("Can I get an airport transfer from the villa?") == "special_request"

def test_classify_general():
    assert classify_query("Do you allow pets?") == "general_enquiry"


# ─────────────────────────────────────────────────────────
# Schema normalisation tests
# ─────────────────────────────────────────────────────────

def test_normalise_message():
    payload = InboundMessage(
        source="whatsapp",
        guest_name="Rahul Sharma",
        message="Is the villa available?",
        timestamp=datetime(2026, 5, 5, 10, 30, 0),
        booking_ref="NIS-2024-0891",
        property_id="villa-b1",
    )
    result = normalise_message(payload, "pre_sales_availability")
    assert result["source"] == "whatsapp"
    assert result["guest_name"] == "Rahul Sharma"
    assert result["query_type"] == "pre_sales_availability"
    assert "message_id" in result
    assert len(result["message_id"]) == 36  # UUID length


# ─────────────────────────────────────────────────────────
# Webhook endpoint integration tests (Claude mocked)
# ─────────────────────────────────────────────────────────

MOCK_REPLY = "Hi Rahul! Great news — Villa B1 is available from April 20–24. The base rate is INR 18,000/night for up to 4 guests. Shall I hold it for you?"
MOCK_CONFIDENCE = 0.92


@pytest.fixture
def mock_claude():
    with patch("app.main.get_claude_reply", new_callable=AsyncMock) as m:
        m.return_value = (MOCK_REPLY, MOCK_CONFIDENCE)
        yield m


# Test 1 — availability query (WhatsApp)
def test_availability_whatsapp(mock_claude):
    payload = {
        "source": "whatsapp",
        "guest_name": "Rahul Sharma",
        "message": "Is the villa available from April 20 to 24? What is the rate for 2 adults?",
        "timestamp": "2026-05-05T10:30:00Z",
        "booking_ref": "NIS-2024-0891",
        "property_id": "villa-b1",
    }
    resp = client.post("/webhook/message", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["query_type"] == "pre_sales_availability"
    assert data["action"] == "auto_send"
    assert data["confidence_score"] == MOCK_CONFIDENCE
    assert "message_id" in data


# Test 2 — complaint (Booking.com) → should always escalate
def test_complaint_escalates(mock_claude):
    mock_claude.return_value = ("We sincerely apologise...", 0.45)
    payload = {
        "source": "booking_com",
        "guest_name": "Priya Menon",
        "message": "The AC is not working and the pool is dirty. I am very unhappy.",
        "timestamp": "2026-05-06T08:00:00Z",
        "booking_ref": "NIS-2024-0999",
        "property_id": "villa-b1",
    }
    resp = client.post("/webhook/message", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["query_type"] == "complaint"
    assert data["action"] == "escalate"


# Test 3 — check-in query (Airbnb) → high confidence, auto_send
def test_checkin_query(mock_claude):
    mock_claude.return_value = ("Hi Ankit! Check-in is at 2pm and check-out at 11am. WiFi: Nistula@2024.", 0.97)
    payload = {
        "source": "airbnb",
        "guest_name": "Ankit Verma",
        "message": "What time is check-in? Also, can you share the WiFi password?",
        "timestamp": "2026-05-07T14:00:00Z",
        "booking_ref": "NIS-2024-1001",
        "property_id": "villa-b1",
    }
    resp = client.post("/webhook/message", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["query_type"] == "post_sales_checkin"
    assert data["action"] == "auto_send"


# Test 4 — special request (Instagram) → mid confidence, agent_review
def test_special_request_agent_review(mock_claude):
    mock_claude.return_value = ("Hi Sana! We can arrange an airport pickup. Let me confirm availability.", 0.72)
    payload = {
        "source": "instagram",
        "guest_name": "Sana Khan",
        "message": "Hey! Can you arrange an airport transfer for us on April 20th at 6pm?",
        "timestamp": "2026-05-08T09:00:00Z",
    }
    resp = client.post("/webhook/message", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "agent_review"


# Test 5 — invalid source returns 422
def test_invalid_source():
    payload = {
        "source": "telegram",
        "guest_name": "Test User",
        "message": "Hello",
        "timestamp": "2026-05-05T10:00:00Z",
    }
    resp = client.post("/webhook/message", json=payload)
    assert resp.status_code == 422


# Test 6 — missing required field
def test_missing_message_field():
    payload = {
        "source": "whatsapp",
        "guest_name": "Test User",
        "timestamp": "2026-05-05T10:00:00Z",
    }
    resp = client.post("/webhook/message", json=payload)
    assert resp.status_code == 422


# Test 7 — health check
def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
