"""PII redaction policy for transcription (FR/NFR6).

Debt-collection calls are dense with regulated personal data: the collector reads back a
partial SSN, a mailing address and a balance, and the consumer may give a card number. None
of that may land in our storage, so redaction happens **at the provider, before the
transcript is ever returned to us** — AssemblyAI returns only redacted content unless the
caller explicitly opts into ``redact_pii_return_unredacted``, which this system never does.
What we never receive, we cannot leak.

Two provider constraints shape everything here:

1. ``redact_pii: true`` on its own redacts **nothing** — at least one policy must be listed
   in ``redact_pii_policies``. That is why the policy set below is explicit and exhaustive
   rather than a flag.
2. Redaction is only supported on the Universal speech models, so the model is pinned in
   config instead of relying on the account default (which the provider documents as
   varying between free and paid tiers).

Substitution uses ``entity_name`` (``[PERSON_NAME]``) rather than ``hash`` (``####``) on
purpose: the judge still has to grade *"did the collector identify the company?"*, and a
labelled placeholder preserves the fact that an organization was named while removing which
one. ``####`` would destroy that signal entirely. See ``docs/PII_REDACTION.md``.
"""

from __future__ import annotations

import enum


class PiiMode(enum.StrEnum):
    """How much of a portfolio's data is redacted. Admin-set per portfolio (§2).

    One setting with three levels rather than two independent toggles: redacting the
    recording without redacting the transcript is not a state anyone wants, and two booleans
    would allow it. Read live at processing time, so changing it applies to every call
    processed afterwards — it is not snapshotted onto the call.
    """

    OFF = "OFF"
    #: Redact the transcript. The recording stays as uploaded, so the multimodal judge still
    #: hears the real names and can grade "did the collector identify the company?".
    TRANSCRIPT = "TRANSCRIPT"
    #: Also replace the stored recording with a redacted (beeped) version — but only AFTER
    #: judging, so the judge still gets the original audio. Shrinks PII-at-rest from the full
    #: retention window to roughly the processing time.
    TRANSCRIPT_AND_RECORDING = "TRANSCRIPT_AND_RECORDING"

    @property
    def redacts_transcript(self) -> bool:
        return self is not PiiMode.OFF

    @property
    def redacts_recording(self) -> bool:
        return self is PiiMode.TRANSCRIPT_AND_RECORDING


def parse_pii_mode(raw: str | None) -> PiiMode:
    """Tolerant parse of a stored/sent value; anything unrecognized falls back to OFF."""
    try:
        return PiiMode(str(raw or "").upper())
    except ValueError:
        return PiiMode.OFF

# The policies we send. Grouped by the categories the business asked for, plus the broader
# "any other personal data" sweep. Every name here is a value of the provider's PiiPolicy
# enum — an unknown name is rejected by the API for the whole request, so additions must be
# checked against the provider's policy list, not guessed.
#
# NOT included, deliberately (they would gut the audit without protecting a person):
#   time, duration, date_interval — "we're open 9 to 5", "within 30 days" are call mechanics
#   language, event, statistics, zodiac_sign, blood_type — noise in this domain
#   drug, injury, medical_*, healthcare_number — not present in collections calls, and
#     redacting medical_condition would mangle hardship explanations the coach needs
#   occupation — measured on real calls: it redacts the words "debt collector", destroying
#     the FDCPA mini-Miranda ("this communication is from a debt collector") that the
#     compliance checklist exists to verify. A job title is weak PII, and the identifying
#     part — the employer's NAME — is still removed by `organization`.
# `date` IS included even though it catches payment dates, because a bare date is the most
# common way a date of birth is spoken ("March 4th, 1982") and date_of_birth alone misses it.

_IDENTITY = (
    "person_name",
    "person_age",
    "date_of_birth",
    "us_social_security_number",
    "drivers_license",
    "passport_number",
    "nationality",
    "gender_sexuality",
    "marital_status",
    "religion",
    "political_affiliation",
    "physical_attribute",
)

_CONTACT = (
    "phone_number",
    "email_address",
    "url",
    "ip_address",
    "username",
    "password",
    "location",
    "location_address",
    "location_address_street",
    "location_city",
    "location_state",
    "location_zip",
    "location_country",
    "location_coordinate",
)

_FINANCIAL = (
    "credit_card_number",
    "credit_card_cvv",
    "credit_card_expiration",
    "banking_information",
    "account_number",
    "money_amount",
    "organization",          # creditor / collection agency / employer names
)

_IDENTIFIERS = (
    "number_sequence",       # "last four of your social is 9749", reference numbers
    "date",
    "vehicle_id",
    "filename",
)

#: The default policy set sent with every transcription request.
DEFAULT_PII_POLICIES: tuple[str, ...] = _IDENTITY + _CONTACT + _FINANCIAL + _IDENTIFIERS

#: Substitution style. ``entity_name`` -> "[PERSON_NAME]"; ``hash`` -> "####".
DEFAULT_PII_SUB = "entity_name"

#: Speech models, in priority order. Pinned rather than defaulted because PII redaction is
#: only supported on the Universal models and the provider's own default is documented as
#: differing between account tiers.
DEFAULT_SPEECH_MODELS: tuple[str, ...] = ("universal-3-pro", "universal-2")


def parse_csv_setting(raw: str, fallback: tuple[str, ...]) -> list[str]:
    """Parse a comma-separated env override into a clean list; empty falls back to default."""
    values = [part.strip() for part in raw.split(",") if part.strip()]
    return values or list(fallback)
