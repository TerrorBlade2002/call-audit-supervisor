# ruff: noqa: E501  — SAMPLE is a verbatim checklist fixture; long lines are intentional.
"""Parser for the Everest checklist .txt format (FR6). Pure unit test — no DB."""

from __future__ import annotations

import pytest

from app.checklists.parse import ParseError, parse_checklist

SAMPLE = """Debt Collection Call Evaluation Checklist
A. Compliance and Mandatory Disclosures
Recording Disclosure
Did the collector state the Outbound/Inbound Recording Disclosure?
Response: Yes / No / NA / Other
Comment:
Debtor Identification and Verification
Did the collector identify the debtor correctly?
Audit Notes/Reference: Two forms of verification are required, full name and one secondary identifier, like DOB, SSN, or mailing address.
Response: Yes / No / NA
Comment:
Collector, Company, and Client Identification
Did the collector identify themselves, the company, and the client correctly?
Notes: Company name must be Everest Receivable Services. Client is most likely DNF Associates.
Response: Yes / No / NA
Comment:
Mini-Miranda Disclosure
Did the collector state the Mini-Miranda disclosure?
Response: Yes / No / NA
Comment:
Language Preference Requirement for NY Consumers
If applicable, did the collector address language preference requirements for a New York consumer?
Notes: Identify whether a NY address is mentioned or confirmed during the call.
Response: Yes / No / NA
Comment:
Reg F Consent and Mailing Address Verification
Did the collector obtain Reg F consent and verify the mailing address?
Notes: This includes permission to call back within the next 7 days, where applicable.
Response: Yes / No / NA
Comment:
Update of Bad or Incorrect Contact Information
Did the collector update bad or incorrect mailing address, phone number, email address, or other contact information?
Notes: This should be identified from the call conversation.
Response: Yes / No / NA
Comment:
Payment Authorization
Did the agent obtain proper payment authorization?
Response: Yes / No / NA
Comment:
B. FDCPA and Risk Language Review
Risk Language Avoidance
Did the collector avoid using risk language?
Examples: Unprofessional tone or statements, confrontational language, threatening statements, or inappropriate pressure.
Response: Yes / No / NA
Comment:
False or Misleading Language - FDCPA
Did the collector avoid using false or misleading language?
Response: Yes / No / NA
Comment:
Appropriate Call Purpose Statement
Did the collector state the purpose of the call and ask for the balance in full?
Response: Yes / No / NA
Comment:
C. Call Handling and Escalation
Appropriate Escalation
Did the collector escalate the call appropriately when required?
Response: Yes / No / NA
Comment:
Escalation Due to Objection Handling Issues
If the agent was not able to handle objections properly, did the agent transfer the call to a senior representative or supervisor?
Response: Yes / No / NA
Comment:
Call Closing and Callback Information
Did the agent conclude the call well and provide callback information?
Response: Yes / No / NA
Comment:
D. Sales, Negotiation, and Objection Handling
Creation of Urgency
Did the collector create urgency during the call?
Response: Yes / No / NA
Comment:
Relevant Rebuttals
Did the collector use rebuttals that matched the debtor's objections or questions?
Response: Yes / No / NA
Comment:
Objections Faced by the Agent
What objections were faced by the agent during the call?
Comment:
Inefficiently Handled Objections
Out of the objections faced, which objections was the agent not able to handle efficiently?
Comment:
Consumer Interruptions
Did the agent interrupt the consumer during the call?
Response: Yes / No / NA
Comment:
Confidence in Rebuttal Delivery
Was the rebuttal delivered confidently?
Notes: Check for hesitation, excessive fillers, lack of confidence, or uncertainty.
Response: Yes / No / NA
Comment:
Negotiation Skill
How was the agent's negotiation skill?
Response: Strong / Average / Needs Improvement / NA
Comment:
Full Balance Request Before Negotiation
Did the agent ask for the full balance first before negotiating down?
Response: Yes / No / NA
Comment:
Negotiation Approach and Call Control
Was the agent's approach submissive while negotiating the payment plan, or did the agent maintain call control?
Response: Maintained Call Control / Submissive / NA
Comment:
Effort to Negotiate Better Terms
If the consumer asked for a reduced amount or a long payment plan, did the agent make at least one effort to convince the consumer for a shorter payment period or higher payment amount before agreeing?
Response: Yes / No / NA
Comment:
Handling of Stall Tactics
If the consumer used a stall tactic, such as saying "I'll pay it off next week," did the agent try to set up a post-dated payment, or did the agent simply arrange a callback?
Response: Set Up Post-Dated Payment / Arranged Callback Only / NA
Comment:
"""


def _by_text(items: list, needle: str):
    return next(it for it in items if needle.lower() in it.text.lower())


def test_parses_full_everest_checklist() -> None:
    name, items = parse_checklist(SAMPLE)
    assert name == "Debt Collection Call Evaluation Checklist"
    assert len(items) == 25

    # Four sections, in order, with the letter prefix stripped.
    sections = list(dict.fromkeys(it.section for it in items))
    assert sections == [
        "Compliance and Mandatory Disclosures",
        "FDCPA and Risk Language Review",
        "Call Handling and Escalation",
        "Sales, Negotiation, and Objection Handling",
    ]

    # Plain yes/no/na → objective PASS_FAIL_NA with no stored options + captured guidance.
    debtor = _by_text(items, "identify the debtor correctly")
    assert debtor.answer_type == "PASS_FAIL_NA"
    assert debtor.options is None
    assert debtor.is_subjective is False
    assert "Two forms of verification" in debtor.guidance

    # Yes/No/NA/Other → CHOICE keeping the verbatim options, still objective.
    rec = _by_text(items, "Recording Disclosure")
    assert rec.answer_type == "CHOICE"
    assert rec.options == ["Yes", "No", "NA", "Other"]
    assert rec.is_subjective is False

    # Qualitative scale → CHOICE + subjective.
    neg = _by_text(items, "negotiation skill")
    assert neg.answer_type == "CHOICE"
    assert neg.options == ["Strong", "Average", "Needs Improvement", "NA"]
    assert neg.is_subjective is True

    # No Response line → free-text TEXT item.
    text_items = [it for it in items if it.answer_type == "TEXT"]
    assert len(text_items) == 2
    assert any("objections were faced" in it.text.lower() for it in text_items)


def test_rejects_unparseable_text() -> None:
    with pytest.raises(ParseError):
        parse_checklist("just some random prose\nwith no sections or items at all")
