# Nistula Guest Message Handler

A FastAPI backend that receives guest messages from multiple channels, classifies and normalises them, gets a Claude-drafted reply, and returns a confidence-scored response with a recommended action.

---

## Project Structure

```
nistula/
├── app/
│   ├── main.py           # FastAPI app + webhook endpoint
│   ├── models.py         # Pydantic request/response models
│   ├── classifier.py     # Rule-based query type classifier
│   ├── schema.py         # Message normalisation (→ unified schema)
│   └── claude_handler.py # Claude API call + confidence scoring
├── tests/
│   └── test_webhook.py   # 14 unit + integration tests
├── schema.sql            # Part 2 — PostgreSQL schema
├── requirements.txt
├── run.py                # Dev server entry point
├── .env.example
└── README.md
```

---

## Setup

```bash
# 1. Clone / unzip the project
cd nistula

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set your API key
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...

# 5. Start the server
python run.py
# → running on http://localhost:8000
# → interactive docs at http://localhost:8000/docs
```

---

## Part 1 — Webhook Endpoint

### `POST /webhook/message`

**Request**
```json
{
  "source": "whatsapp",
  "guest_name": "Rahul Sharma",
  "message": "Is the villa available from April 20 to 24? What is the rate for 2 adults?",
  "timestamp": "2026-05-05T10:30:00Z",
  "booking_ref": "NIS-2024-0891",
  "property_id": "villa-b1"
}
```

**Response**
```json
{
  "message_id": "3f7a1c9b-...",
  "query_type": "pre_sales_availability",
  "drafted_reply": "Hi Rahul! Great news — Villa B1 is available from April 20–24...",
  "confidence_score": 0.91,
  "action": "auto_send"
}
```

Valid `source` values: `whatsapp`, `booking_com`, `airbnb`, `instagram`, `direct`

---

### Flow

```
POST /webhook/message
        │
        ▼
 [classifier.py]  rule-based keyword matching → query_type
        │
        ▼
  [schema.py]     add UUID, normalise into unified dict
        │
        ▼
[claude_handler.py]  build prompt with property context
        │             call Claude API
        │             parse JSON reply + confidence
        │             blend with local heuristic
        ▼
  [main.py]       determine action, return WebhookResponse
```

---

### Query Classification

Classification uses ordered regex rules (no extra LLM call needed):

| Type | Trigger keywords |
|---|---|
| `complaint` | not working, broken, unhappy, unacceptable… |
| `post_sales_checkin` | check-in, check-out, wifi, password, key… |
| `special_request` | airport transfer, extra bed, birthday, dietary… |
| `pre_sales_availability` | available, book, dates, month names… |
| `pre_sales_pricing` | rate, price, how much, per night, adults… |
| `general_enquiry` | catch-all |

Rules are evaluated in order — `complaint` wins over everything else, preventing an "AC is not working" from being classified as `general_enquiry`.

---

### Confidence Scoring Logic

Confidence scoring has two components blended together:

**1. Claude self-score (weight: 70%)**  
Claude is instructed to return a `confidence` float 0–1 in its JSON response, based on:
- Can it answer every part of the query from the property context?
- Are there parts requiring external confirmation?
- Is the request ambiguous or multi-part?

**2. Local heuristic (weight: 30%)**  
A per-query-type floor based on how deterministic the answer category is:

| Query type | Local score |
|---|---|
| `post_sales_checkin` | 1.00 — all facts are in the context |
| `pre_sales_availability` / `_pricing` | 0.90 — mostly deterministic |
| `general_enquiry` / `special_request` | 0.75 — may require coordination |
| `complaint` | 0.40 — always needs human judgment |

**Blending:**  
`final = 0.7 × claude_score + 0.3 × local_score`

**Complaint cap:**  
Regardless of blended score, complaints are capped at 0.55 to guarantee `escalate` action.

**Action thresholds:**

| Score | Action |
|---|---|
| ≥ 0.85 | `auto_send` |
| 0.60 – 0.84 | `agent_review` |
| < 0.60 or complaint | `escalate` |

*Why blend instead of trusting Claude alone?* LLMs can be overconfident. If Claude self-reports 0.95 on a complaint, the local cap still forces escalation. The 70/30 split was chosen to give Claude's contextual judgment meaningful weight while preserving deterministic overrides for known edge cases.

---

### Claude Prompt Design

The system prompt instructs Claude to:
- Address the guest by first name
- Answer every question from the property context (never invent facts)
- Match tone to channel (WhatsApp = casual, OTA = formal)
- For complaints: acknowledge empathetically, promise follow-up, don't admit liability
- Return **only valid JSON**: `{"reply": "...", "confidence": 0.0}`

The user prompt includes: channel, guest name, booking ref, property, query type, and the message text.

---

### Error Handling

- Missing `ANTHROPIC_API_KEY` → `RuntimeError` on startup path
- Claude API non-2xx → 502 with detail
- Claude returns malformed JSON → falls back to raw text as reply, confidence 0.5
- Invalid `source` field → 422 Unprocessable Entity (Pydantic validation)
- Any unhandled exception → 500 with detail via global exception handler

---

## Part 2 — Database Schema

See `schema.sql` for the full `CREATE TABLE` statements.

### Tables

| Table | Purpose |
|---|---|
| `properties` | Reference data for each villa/property |
| `guests` | One canonical record per guest across all channels |
| `reservations` | A stay, linked to guest + property |
| `conversations` | Thread grouping messages by guest + channel |
| `messages` | Every inbound and outbound message in one table |
| `ai_draft_log` | Immutable audit of every Claude API call |

### Key Design Decisions

**Guest identity across channels**  
Email (`citext`, case-insensitive) is the canonical key. Channel-specific IDs (whatsapp_id, airbnb_guest_id, etc.) are nullable columns with partial unique indexes. A guest who books on Airbnb and then messages on WhatsApp can be merged manually or via a future deduplication job. An alternative would be a separate `guest_identities` junction table — cleaner for N channels but adds a join to every message lookup.

**Single messages table (not inbound/outbound split)**  
All messages share one table with a `direction` enum and nullable columns for AI-specific fields. This makes timeline queries and conversation threading trivial (`ORDER BY sent_at`) without joins. The trade-off is sparse nullable columns for inbound rows (no `draft_status`) and outbound rows (no `query_type`). At Nistula's scale this is fine; at 10M+ messages/day a partitioned or split approach would be worth the join cost.

**Separate `ai_draft_log` table**  
Raw prompts and responses are kept out of the `messages` table to avoid bloating hot read paths. Every Claude call is logged immutably here for debugging, cost tracking, and confidence calibration over time.

**The hardest design decision**

*How to model guest identity across channels without forcing an immediate merge.*

The temptation is to create a `guest_identities` table with `(guest_id, channel, external_id)` rows — fully normalised. But in practice, a guest messaging from WhatsApp may be a different person than the Booking.com guest with the same name, until a human confirms the merge. Premature auto-merging creates customer data corruption that is hard to undo.

The chosen approach — nullable channel-ID columns on `guests` with unique constraints — keeps the canonical record simple. It forces explicit identity resolution (a human or a matching job sets the external ID columns) rather than creating ghost duplicates. The cost is a slightly denormalised `guests` table. I think that trade-off is correct at this stage: data integrity wins over schema elegance when the domain involves real guest data.

---

## Running Tests

```bash
pytest tests/ -v
```

All 14 tests pass without a live API key (Claude calls are mocked at the integration test layer).

---

## curl Examples + Real Outputs

**Test 1 — Pre-sales availability (WhatsApp) → `auto_send`**
```bash
curl -X POST http://localhost:8000/webhook/message \
  -H "Content-Type: application/json" \
  -d '{
    "source": "whatsapp",
    "guest_name": "Rahul Sharma",
    "message": "Is the villa available from April 20 to 24? What is the rate for 2 adults?",
    "timestamp": "2026-05-05T10:30:00Z",
    "booking_ref": "NIS-2024-0891",
    "property_id": "villa-b1"
  }'
```
```json
{
  "message_id": "3f7a1c9b-...",
  "query_type": "pre_sales_availability",
  "drafted_reply": "Hi Rahul! Yes, Villa B1 is available from April 20-24. For 2 adults, the rate would be INR 18,000 per night (our base rate covers up to 4 guests). So for 4 nights, the total would be INR 72,000. The villa has 3 bedrooms, a private pool, and our caretaker is available 8am-10pm. Would you like me to hold these dates for you?",
  "confidence_score": 0.97,
  "action": "auto_send"
}
```

**Test 2 — Complaint (Booking.com) → `escalate`**
```bash
curl -X POST http://localhost:8000/webhook/message \
  -H "Content-Type: application/json" \
  -d '{
    "source": "booking_com",
    "guest_name": "Priya Menon",
    "message": "The AC is not working and the pool is dirty. I am very unhappy.",
    "timestamp": "2026-05-06T08:00:00Z",
    "booking_ref": "NIS-2024-0999",
    "property_id": "villa-b1"
  }'
```
```json
{
  "message_id": "9a2c3d1e-...",
  "query_type": "complaint",
  "drafted_reply": "Dear Priya, I sincerely apologize for the inconvenience with the AC and pool condition. This is certainly not the experience we want for our guests at Villa B1. I am immediately coordinating with our caretaker team to address both issues as a priority. Someone will be at the villa within the next hour to fix the AC and clean the pool thoroughly. I will personally follow up with you once both issues are resolved. Thank you for bringing this to our attention.",
  "confidence_score": 0.33,
  "action": "escalate"
}
```

**Test 3 — Post-sales check-in info (Airbnb) → `auto_send`**
```bash
curl -X POST http://localhost:8000/webhook/message \
  -H "Content-Type: application/json" \
  -d '{
    "source": "airbnb",
    "guest_name": "Ankit Verma",
    "message": "What time is check-in? Also what is the WiFi password?",
    "timestamp": "2026-05-07T14:00:00Z",
    "booking_ref": "NIS-2024-1001",
    "property_id": "villa-b1"
  }'
```
```json
{
  "message_id": "7dde5bd2-...",
  "query_type": "post_sales_checkin",
  "drafted_reply": "Hi Ankit, check-in is at 2pm and the WiFi password is Nistula@2024. Looking forward to welcoming you to Villa B1!",
  "confidence_score": 1.0,
  "action": "auto_send"
}
```

**Test 4 — Special request (Instagram, no booking ref) → `escalate`**
```bash
curl -X POST http://localhost:8000/webhook/message \
  -H "Content-Type: application/json" \
  -d '{
    "source": "instagram",
    "guest_name": "Sana Khan",
    "message": "Hey! Can you arrange an airport transfer from Goa airport on April 20th at 6pm?",
    "timestamp": "2026-05-08T09:00:00Z"
  }'
```
```json
{
  "message_id": "1031f6ab-...",
  "query_type": "special_request",
  "drafted_reply": "Hi Sana! I'd be happy to help arrange an airport transfer for you on April 20th at 6pm. Let me check with our local transport partners and get back to you shortly with options and pricing. Could you also let me know how many guests will need the transfer? Looking forward to hosting you at Villa B1!",
  "confidence_score": 0.57,
  "action": "escalate"
}
```
