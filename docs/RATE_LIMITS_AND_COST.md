# Rate Limits, Caps, Batching & Cost — Derivation (NFR3 / §14)

This document derives every operational number in `app/config.py`. **All of them are
environment-overridable** — change a value in Railway variables and restart; no code
change, no redeploy of logic. The defaults are sized for the PRD reference volume with
deliberate safety margin under vendor quotas.

> ⚠️ Two inputs are unverified per §19 and **must be confirmed before they bind cost**:
> `gemini-3.1-pro` pricing/availability on the Developer API, and your contracted
> AssemblyAI async rate. The numbers below use the PRD's proxy figures. When you confirm
> the real ones, update the env vars — the *shape* of the plan does not change.

---

## 1. Volume model

| Quantity | Value | Source |
|---|---|---|
| Calls / month (full) | 13,200 | 2 portfolios × 10 agents × 30/day × 22 days |
| Calls / working day | ~600 | 13,200 ÷ 22 |
| Calls / portfolio / day | ~300 | 600 ÷ 2 |
| Avg call length | 5 min | §5 |
| Audio-hours / month | ~1,100 | 13,200 × 5 min |
| Pilot target | ~1,500–2,000 calls/mo | §14.2 (≤ $100) |

Working days are bursty: 600 calls are not spread evenly across 24h. Assume the realistic
worst case is the daily volume submitted within a **4-hour upload window** → ~150 calls/hr
→ **~2.5 calls/min** sustained, with short bursts higher. The limiters are sized for the
burst, the daily cap bounds the total.

---

## 2. Per-call work profile

One call consumes, across its lifecycle:

| Step | External call | Magnitude |
|---|---|---|
| Transcription | 1 × AssemblyAI async submit (+ webhook in) | 1 in-flight transcript, ~5 min audio |
| Judge | 1 × Gemini structured call over whole checklist | ~10K input + ~3K output tokens |
| Escalation (v1) | 0 LLM calls (single tier → human-review flag) | — |
| Objection embeddings | batched Gemini embeddings | small |
| Narrative (lazy) | 0–1 × Gemini, only on report open | ~6K in + ~4K out, **not per call** |
| Rubric distillation | 1 × Gemini **per checklist version**, not per call | amortized to ~0 |

So steady-state per-call LLM load ≈ **one judge call (~13K tokens)**. The expensive
narrative is lazy (§7.4) and the rubric is distilled once per checklist version (§7.2) —
both are deliberate cost levers, not per-call costs.

**Token math (judge, sustained burst):** 2.5 calls/min × 13K = **~32.5K tokens/min**.
That is ~3% of a 1M TPM budget — Gemini's *token* limit is not the binding constraint.
The binding constraints are **requests/day**, **concurrency** (don't open 600 sockets at
once), and **cost**. The limiter is therefore mostly protecting against retry storms and
re-processing bursts, plus giving us a clean place to throttle if a tier quota is tighter
than assumed.

---

## 3. The limits and why (defaults in `config.RateLimitSettings`)

### Gemini (LLM judge)
| Var | Default | Rationale |
|---|---|---|
| `GEMINI_RPM` | 120 | ~48× the ~2.5 calls/min steady rate — absorbs bursts while staying under a typical paid Tier-1 RPM. Lower it if your tier is tighter. |
| `GEMINI_TPM` | 1,000,000 | Proxy from 2.5-Pro tier; judge load is ~3% of this. Headroom for narrative bursts. |
| `GEMINI_RPD` | 8,000 | Soft daily request guard. 600 judge calls/day + narratives + distillation sit well under it. |
| `GEMINI_MAX_CONCURRENCY` | 8 | Bounds in-flight LLM sockets per worker. 8 × ~10s/call ≈ 48 calls/min capacity ≫ demand. |

### AssemblyAI (STT)
| Var | Default | Rationale |
|---|---|---|
| `AAI_MAX_INFLIGHT` | 32 | Concurrent in-flight async transcripts. Submit-and-webhook means these aren't busy threads; 32 clears a 150-call hour comfortably as transcripts finish and webhooks drain. Sits under typical account concurrency (often 200). |
| `AAI_RPM` | 60 | Submission requests/min. 2.5/min steady; 60 absorbs a batch-upload burst. |

### Per-portfolio daily cap
| Var | Default | Rationale |
|---|---|---|
| `DAILY_CAP_PER_PORTFOLIO` | 300 | = full-volume per-portfolio/day. **For the $100 pilot, set ~85** (one portfolio, ~8–9 recordings/agent/day) — see §14.2. Over-cap calls **defer**, they don't fail. |

### Retry / backoff
| Var | Default | Rationale |
|---|---|---|
| `RETRY_MAX_ATTEMPTS` | 5 | Ladder 2→4→8→16→32s (±25% jitter) ≈ up to ~1 min of retries before dead-letter. |
| `RETRY_BASE_SECONDS` | 2.0 | First retry waits ~2s. |
| `RETRY_CAP_SECONDS` | 300 | A single wait never exceeds 5 min (a 429 with a long quota window won't park a job for hours). |
| `RETRY_JITTER_RATIO` | 0.25 | ±25% jitter decorrelates concurrent retries (avoids thundering herd after a shared 429). |

### Worker loop
| Var | Default | Rationale |
|---|---|---|
| `WORKER_CLAIM_BATCH` | 8 | Matches `GEMINI_MAX_CONCURRENCY`; the loop never claims more than it can work. |
| `WORKER_LEASE_SECONDS` | 300 | Lease > longest single step. Crash → lease expires in ≤5 min → job reclaimed. |
| `RECONCILER_INTERVAL_SECONDS` | 30 | Sweep every 30 sec for overdue transcripts / stuck leases. |
| `RECONCILER_TRANSCRIPT_OVERDUE_SECONDS` | 60 | A transcript with no webhook after 1 min is polled directly (lost-webhook recovery). |

---

## 4. Batching strategy

- **Upload batching** (product): ≤10 files/agent/batch (NFR2). Each file becomes its own
  `call` + `job` row — batching is a UX grouping (`batch_id`), **not** a single
  coarse-grained job. This keeps retry/idempotency at per-call granularity (one bad file
  never fails a batch) and lets the daily cap defer the *tail* of an over-cap batch while
  admitting the rest.
- **Embedding batching** (cost): objection embeddings are sent to Gemini in batched
  requests per report rather than one request per objection — fewer RPM tokens, same TPM.
- **No transcript batching**: AssemblyAI async is one submit per audio file by design;
  concurrency (not batching) is the throughput lever there.

---

## 5. Graceful degradation ladder (what happens under pressure)

1. **Normal:** token buckets shape traffic; nobody waits on a vendor.
2. **Local burst:** RPM/TPM buckets and the concurrency semaphore make the worker *wait*
   (not error). Jobs stay `PENDING_*`, picked up as capacity frees.
3. **Vendor 429:** `RateLimitError` → honor `Retry-After` if present, else exponential
   backoff with jitter. In-call retries for blips; the durable worker re-queues with
   `next_attempt_at` for longer waits — survives a process restart.
4. **Repeated failure:** after `RETRY_MAX_ATTEMPTS`, job → `FAILED` (dead-letter) + alert.
   No silent loss; every job reaches a terminal state (NFR4).
5. **Over daily cap:** job **deferred** (re-queued for next window) with a user-visible
   "daily limit reached" — never failed. Bounds worst-case spend (§14.3 lever 5).
6. **Systemic (escalation fraction > `max_escalation_fraction`):** circuit breaker — flag
   the call for review instead of escalating everything (§7.3.4).

---

## 6. Cost envelope (proxy pricing — reconfirm per §19)

Per the PRD: **~$0.038–0.048 per 5-min call** all-in (lean→rich).

| Scenario | Calls/mo | Est. variable | + fixed (~$30) | Total |
|---|---|---|---|---|
| **Pilot (lean)** | ~2,000 | ~$80 | ~$30 | **~$100** ✅ |
| Full (lean, narrative lazy) | 13,200 | ~$495 | ~$30 | ~$520–565 |
| Full (rich, narrative always) | 13,200 | ~$760 | ~$30 | ~$790–830 |

**Levers (priority order, §14.3):** lazy narrative → distilled rubric → add a Flash tier
(the routing seam already exists; prepend a tier in config) → AssemblyAI webhooks (no idle
billing) → daily caps. To scale the pilot up: **raise `DAILY_CAP_PER_PORTFOLIO`** — a
config change, not an engineering one.

---

## 7. Multi-instance note

At pilot scale a single worker makes the in-process token buckets authoritative. For N
workers, the binding shared limit is the **daily cap**, which is already a Postgres atomic
counter (`daily_usage`) and therefore correct across instances today. The RPM/TPM buckets
would move to a shared Postgres/Redis counter — the `ProviderLimiter` interface stays
identical, so it's a drop-in (§8.6). The per-call concurrency semaphore stays per-process
(it's about local sockets), with `N × GEMINI_MAX_CONCURRENCY` total — size accordingly.
