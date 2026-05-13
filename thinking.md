**Question A — The Immediate Response**

> "Hi [Name], I'm so sorry — this is completely unacceptable and I'm on it right now. I've alerted our caretaker to come to the villa immediately to fix the hot water. I'll follow up with you in 20 minutes with an update. Regarding the refund, I've flagged it for our team and you'll hear from us first thing in the morning."

**Why this wording:** Acknowledges urgency without over-promising ("I'll fix it" vs "I'm on it"). Gives a concrete 20-minute callback so they feel action is happening. Separates the refund from the immediate problem — don't resolve money at 3am, but don't ignore it either.

---

**Question B — The System Response**

Beyond sending the message, the platform should:

- **Classify** as `complaint` → confidence capped at 0.55 → `escalate` immediately, never auto-send
- **Page the caretaker** via WhatsApp/SMS with the exact issue and villa location — not just a notification, a message requiring acknowledgement
- **Log** the full incident in `ai_draft_log` and create a complaint record in `messages` with `query_type: complaint`, timestamp, and confidence score
- **Start a 30-minute timer.** If no human agent marks the conversation as handled, the system auto-escalates to the property manager's personal phone via call, not just a ping
- **Flag the reservation** in `reservations` with a `complaint_open` status so any morning handoff is visible instantly
- **Draft a refund assessment** for the agent to approve in the morning — not auto-issued, but pre-prepared so the agent isn't starting from scratch

If no human responds within 30 minutes, escalate to the next person in a defined on-call chain until someone acknowledges.

---

**Question C — The Learning**

If there are three hot water problems in a single month then it is a maintenance problem and nothing related to guest. Therefore the caretaker and the maintenance person needs to be notified so that they can fix that on an unrgent basis. 
The system should:

- **Tag all three incidents** with a `recurring_issue` flag linked to `property_id: villa-b1` and issue type `hot_water`. If a complaint was raised then the `complaint_id:` should also be mentioned. 
- **Live tracker** should be present keeping the property owner in loop so that he can also track the progress.
- **Trigger a maintenance alert** to the property manager after the second occurrence, not the third
- **Generate a weekly property health report** — complaints grouped by property, issue type, and frequency. If any issue appears more than once in 30 days, it surfaces automatically
- **Add a pre-arrival checklist item** for Villa B1 specifically: caretaker verifies hot water before every check-in until the issue is formally resolved and closed
- **Build a feedback loop** — once the boiler is repaired, log the fix against the complaint cluster so the system knows the pattern is resolved and can detect if it recurs
