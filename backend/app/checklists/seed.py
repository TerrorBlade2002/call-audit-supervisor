# ruff: noqa: E501  — seed data: long item literals are clearer on one line each.
"""The default Everest debt-collection evaluation checklist (FR6.1), shipped per portfolio.

This is **data, not logic** (§3): the exact Everest "Call Evaluation Checklist" (4 sections,
25 items). Each item carries its verbatim answer ``options`` for display; the judge also
emits a normalized PASS/FAIL/NA verdict that drives routing/coloring (so "Did the agent
interrupt? Yes" → FAIL even though the raw answer is "Yes"). ``risk`` + ``is_subjective``
drive the routing layer (§7.3). Managers edit/replace it via the builder.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


class SeedItem(TypedDict):
    section: str
    text: str
    answer_type: str           # CHOICE | TEXT
    options: NotRequired[list[str]]
    is_subjective: NotRequired[bool]
    risk: NotRequired[str]      # NORMAL | ELEVATED | CRITICAL
    guidance: NotRequired[str]


DEFAULT_CHECKLIST_NAME = "Everest Default"

_YNNA = ["Yes", "No", "NA"]

_A = "A · Compliance & Mandatory Disclosures"
_B = "B · FDCPA & Risk Language"
_C = "C · Call Handling & Escalation"
_D = "D · Sales, Negotiation & Objection Handling"

DEFAULT_ITEMS: list[SeedItem] = [
    # --- A. Compliance and Mandatory Disclosures ---
    {"section": _A, "text": "Recording Disclosure stated (Outbound/Inbound)", "answer_type": "CHOICE", "options": ["Yes", "No", "NA", "Other"], "risk": "CRITICAL", "guidance": "Collector must state the call-recording disclosure."},
    {"section": _A, "text": "Debtor identified and verified", "answer_type": "CHOICE", "options": _YNNA, "risk": "CRITICAL", "guidance": "Two forms of verification required: full name + one secondary identifier (DOB, SSN, or mailing address)."},
    {"section": _A, "text": "Collector, company, and client identified", "answer_type": "CHOICE", "options": _YNNA, "risk": "ELEVATED", "guidance": "Company must be Everest Receivable Services. Client is most likely DNF Associates."},
    {"section": _A, "text": "Mini-Miranda disclosure stated", "answer_type": "CHOICE", "options": _YNNA, "risk": "CRITICAL", "guidance": "'This is an attempt to collect a debt and any information obtained will be used for that purpose.'"},
    {"section": _A, "text": "Language preference addressed for NY consumer (if applicable)", "answer_type": "CHOICE", "options": _YNNA, "risk": "ELEVATED", "guidance": "Identify whether a NY address is mentioned/confirmed; if so, address language preference requirements."},
    {"section": _A, "text": "Reg F consent obtained and mailing address verified", "answer_type": "CHOICE", "options": _YNNA, "risk": "CRITICAL", "guidance": "Includes permission to call back within the next 7 days, where applicable."},
    {"section": _A, "text": "Bad/incorrect contact information updated", "answer_type": "CHOICE", "options": _YNNA, "risk": "NORMAL", "guidance": "Update bad/incorrect mailing address, phone, email, or other contact info surfaced in the call."},
    {"section": _A, "text": "Proper payment authorization obtained", "answer_type": "CHOICE", "options": _YNNA, "risk": "CRITICAL", "guidance": "Authorization captured correctly (NA if no payment taken)."},
    # --- B. FDCPA and Risk Language Review ---
    {"section": _B, "text": "Risk language avoided", "answer_type": "CHOICE", "options": _YNNA, "is_subjective": True, "risk": "CRITICAL", "guidance": "Avoid unprofessional/confrontational/threatening statements or inappropriate pressure. Yes = avoided (good)."},
    {"section": _B, "text": "False or misleading language avoided (FDCPA)", "answer_type": "CHOICE", "options": _YNNA, "risk": "CRITICAL", "guidance": "Yes = avoided false/deceptive/misleading statements (good)."},
    {"section": _B, "text": "Call purpose stated and balance in full requested", "answer_type": "CHOICE", "options": _YNNA, "risk": "ELEVATED", "guidance": "State the purpose of the call and ask for the balance in full."},
    # --- C. Call Handling and Escalation ---
    {"section": _C, "text": "Call escalated appropriately when required", "answer_type": "CHOICE", "options": _YNNA, "risk": "NORMAL", "guidance": "Escalate when the situation requires it."},
    {"section": _C, "text": "Escalated to senior/supervisor on objection-handling issues", "answer_type": "CHOICE", "options": _YNNA, "risk": "NORMAL", "guidance": "If unable to handle objections, transfer to a senior representative or supervisor."},
    {"section": _C, "text": "Call closed well with callback information", "answer_type": "CHOICE", "options": _YNNA, "risk": "NORMAL", "guidance": "Conclude the call well and provide callback information."},
    # --- D. Sales, Negotiation, and Objection Handling ---
    {"section": _D, "text": "Urgency created during the call", "answer_type": "CHOICE", "options": _YNNA, "risk": "NORMAL", "guidance": "Create urgency to drive resolution."},
    {"section": _D, "text": "Rebuttals relevant to the consumer's objections", "answer_type": "CHOICE", "options": _YNNA, "is_subjective": True, "risk": "NORMAL", "guidance": "Rebuttals should match the debtor's objections/questions."},
    {"section": _D, "text": "Objections faced by the agent", "answer_type": "TEXT", "risk": "NORMAL", "guidance": "List the objections the consumer raised during the call (feeds the objection dashboard)."},
    {"section": _D, "text": "Objections the agent could not handle efficiently", "answer_type": "TEXT", "risk": "NORMAL", "guidance": "Out of the objections faced, which were not handled efficiently."},
    {"section": _D, "text": "Agent did not interrupt the consumer", "answer_type": "CHOICE", "options": _YNNA, "is_subjective": True, "risk": "ELEVATED", "guidance": "Did the agent interrupt the consumer? Interrupting is a deficiency — normalize interrupting to FAIL."},
    {"section": _D, "text": "Rebuttal delivered confidently", "answer_type": "CHOICE", "options": _YNNA, "is_subjective": True, "risk": "NORMAL", "guidance": "Check for hesitation, excessive fillers, lack of confidence, or uncertainty."},
    {"section": _D, "text": "Negotiation skill", "answer_type": "CHOICE", "options": ["Strong", "Average", "Needs Improvement", "NA"], "is_subjective": True, "risk": "NORMAL", "guidance": "Rate the agent's negotiation skill. Needs Improvement → FAIL; Strong/Average → PASS."},
    {"section": _D, "text": "Full balance requested before negotiating down", "answer_type": "CHOICE", "options": _YNNA, "risk": "NORMAL", "guidance": "Ask for the full balance first before negotiating down."},
    {"section": _D, "text": "Negotiation approach and call control", "answer_type": "CHOICE", "options": ["Maintained Call Control", "Submissive", "NA"], "is_subjective": True, "risk": "NORMAL", "guidance": "Maintained Call Control → PASS; Submissive → FAIL."},
    {"section": _D, "text": "Effort to negotiate better terms", "answer_type": "CHOICE", "options": _YNNA, "risk": "NORMAL", "guidance": "If the consumer asked for a reduced amount/longer plan, did the agent make ≥1 effort for a shorter period or higher amount before agreeing?"},
    {"section": _D, "text": "Handling of stall tactics", "answer_type": "CHOICE", "options": ["Set Up Post-Dated Payment", "Arranged Callback Only", "NA"], "risk": "NORMAL", "guidance": "On a stall ('I'll pay next week'), did the agent set up a post-dated payment? Set Up Post-Dated Payment → PASS; Arranged Callback Only → FAIL."},
]
