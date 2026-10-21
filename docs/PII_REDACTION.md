# PII Redaction (NFR6)

> **Set per portfolio by a super admin**, not globally. Portfolios → hover a portfolio →
> **PII**. Three levels: `OFF` (default), `TRANSCRIPT`, `TRANSCRIPT_AND_RECORDING`. The level
> is read when a call is *processed*, so a change governs every call processed after it;
> calls already processed are untouched.

Debt-collection calls carry regulated personal data by nature: the collector reads back a
partial SSN and a mailing address to verify identity, states a balance, and the consumer may
read out a card number. **No unredacted transcript is ever written to storage.**

## How the guarantee works

Redaction happens **at the provider, before the transcript reaches this system**.

```
audio ──► AssemblyAI (transcribe + redact) ──► redacted text only ──► R2 / Postgres / UI
                                               ▲
                        the raw text never crosses this line
```

`redact_pii_return_unredacted` is deliberately never sent. Left off, AssemblyAI returns only
redacted content — so the original text never enters this process and therefore cannot be
written to object storage, shown in the UI, or sent to the judge. **What we never receive, we
cannot leak.**

Two further checks back that up:

| Where | Check |
|---|---|
| [`stt/assemblyai.py`](../backend/app/stt/assemblyai.py) `fetch_transcript` | Raises `FatalError` if the provider's response is not marked `redact_pii` |
| [`orchestration/handlers.py`](../backend/app/orchestration/handlers.py) | Refuses to call `put_transcript` for a transcript not marked redacted |

The second is deliberate duplication: that line is the one that writes to R2, so a future STT
client cannot bypass the guarantee by forgetting its own check. A job that trips either fails
to `FAILED` rather than storing PII — failing loudly is the correct outcome here.

## Speech model

`speech_models` is **pinned** to `universal-3-pro,universal-2` rather than left to the account
default. Two reasons: PII redaction is only supported on the Universal models, and the
provider documents its own default as differing between account tiers. The singular
`speech_model` field is deprecated in favour of the plural array.

## Policies

`redact_pii: true` **redacts nothing on its own** — at least one policy must be listed in
`redact_pii_policies`. The full set lives in
[`stt/redaction.py`](../backend/app/stt/redaction.py) and covers:

| Business requirement | Policies |
|---|---|
| Consumer full name | `person_name`, `person_age` |
| Date of birth | `date_of_birth`, `date` |
| SSN | `us_social_security_number`, `number_sequence` |
| Debt / financial / creditor / company names | `organization`, `money_amount`, `banking_information`, `account_number` |
| All phone numbers | `phone_number` |
| All emails | `email_address` |
| Card number, CVV, expiry | `credit_card_number`, `credit_card_cvv`, `credit_card_expiration` |
| Other personal data | address/city/state/zip/country, `url`, `ip_address`, `username`, `password`, `drivers_license`, `passport_number`, `nationality`, `religion`, `marital_status`, `gender_sexuality`, `political_affiliation`, `physical_attribute`, `vehicle_id`, `filename` |

Substitution is `entity_name`, so redacted spans read `[PERSON_NAME]` rather than `####`. This
is load-bearing: the judge still has to grade *"did the collector identify the company?"*, and
a labelled placeholder preserves that an organization **was** named while removing which one.
`hash` would destroy that signal.

### Deliberate exclusions

Four policy groups are **not** applied, because they would damage the audit without protecting
anyone:

- **`occupation`** — measured on real calls, it redacts the words *"debt collector"*, destroying
  the FDCPA mini-Miranda (*"this communication is from a debt collector"*) that the compliance
  checklist exists to verify. A job title is weak PII, and the identifying part — the employer's
  **name** — is still removed by `organization`.
- **`time`, `duration`, `date_interval`** — "we're open 9 to 5", "within 30 days" are call
  mechanics, not personal data.
- **medical policies** (`medical_condition`, `drug`, `injury`, `healthcare_number`) — redacting
  these would mangle the hardship explanations the coaching report is meant to capture.
- **`language`, `event`, `statistics`, `zodiac_sign`, `blood_type`** — noise in this domain.

## What this costs the audit

Redaction is not free. Two checklist items become harder to grade, and this is an accepted
trade-off, not an oversight:

- **"Did the collector identify the company correctly?"** The transcript shows `[ORGANIZATION]`,
  so the judge can confirm a company *was* named but not that it was the right one.
- **"Did the collector state the correct balance?"** `[MONEY_AMOUNT]` hides the figure.

If either must be gradable, drop `organization` / `money_amount` from
`ASSEMBLYAI_REDACT_PII_POLICIES` — but understand that puts creditor names and balances back
into stored transcripts.

## ⚠️ What is NOT redacted

**The recording itself.** Redaction covers the transcript's `text`, `words` and `utterances`.
The original audio is still stored in R2 and is still sent to the Gemini judge, which is
multimodal and listens to it. Anyone who can download a recording hears the unredacted PII.

AssemblyAI can produce redacted audio (`redact_pii_audio`, beeped or silenced, available for 24
hours after transcription). Adopting it means fetching that file and replacing the R2 object
after transcription, and accepting that the judge then grades beeped audio. That is a separate
change — see the limitation noted above before treating the pipeline as fully PII-free.

## The three levels

| Level | Transcript | Stored recording | Use when |
|---|---|---|---|
| `OFF` *(default)* | raw | raw | The client has no redaction requirement |
| `TRANSCRIPT` | redacted | raw | Standard. The judge still hears real names, so it can grade "did the collector identify the company?" |
| `TRANSCRIPT_AND_RECORDING` | redacted | **beeped, after judging** | The client requires no PII at rest |

`TRANSCRIPT_AND_RECORDING` swaps the recording **only once judging has finished**, writing the
beeped copy over the same object key. Until then the judge gets the original audio — that
ordering is the whole point, and it is why the swap sits outside the judge's `try/finally`
(a failed judge is retried and must still find unredacted audio). The swap is best-effort: if
the provider has no redacted copy, the transcript is still redacted and the retention sweep
still deletes the audio on schedule, so failing the call would cost more than it protects.

**`OFF` is the default, including for portfolios that existed before this shipped.** Redaction
is an explicit per-client decision; nothing switches itself on. Note this was a change in
posture — redaction used to be a global flag that was on for everyone.

## Configuration

The level is a database column (`portfolios.pii_mode`), set through the UI or
`PATCH /portfolios/{id}/pii-mode` (super admin only, audited). The variables below only
configure *how* redaction behaves when a portfolio asks for it:

| Variable | Default | Notes |
|---|---|---|
| `ASSEMBLYAI_REDACT_PII` | `true` | Fallback default for callers that don't pass a per-call value. The portfolio level is what decides in practice. |
| `ASSEMBLYAI_REDACT_PII_POLICIES` | *(empty → built-in set)* | Comma-separated override. Empty is treated as "use defaults", never sent empty. |
| `ASSEMBLYAI_REDACT_PII_SUB` | `entity_name` | or `hash` |
| `ASSEMBLYAI_SPEECH_MODELS` | `universal-3-pro,universal-2` | Must be models that support redaction. |

## Verification

`backend/tests/test_pii_redaction.py` covers both halves offline (no provider spend): that the
request actually redacts, and that unredacted text is refused at both gates.

This was also validated against real collections calls. Every value matched by pattern in the
originals — emails, phone numbers, balances, ZIP, street address — was absent from the redacted
output, which carried `[PERSON_NAME]`, `[US_SOCIAL_SECURITY_NUMBER]`, `[DATE_OF_BIRTH]`,
`[EMAIL_ADDRESS]`, `[PHONE_NUMBER]`, `[ACCOUNT_NUMBER]`, `[MONEY_AMOUNT]`, `[ORGANIZATION]`,
`[LOCATION_ADDRESS]` and `[LOCATION_CITY]`, while the mini-Miranda survived intact.
