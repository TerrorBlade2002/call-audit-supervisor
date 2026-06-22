"""Judge + routing + objection clustering (Phase 5, §7.3/§7.5).

  * schema.py    — structured judge output (verdicts + objections), schema-validated.
  * routing.py   — the routing layer (pure §7.3): escalate / flag / circuit-breaker.
  * client.py    — JudgeClient Protocol + stub + Gemini structured-output judge.
  * embeddings.py— Embedder Protocol + stub + Gemini embeddings.
  * service.py   — judge a call: load → evaluate → route → assemble + persist report.
  * clustering.py— portfolio objection clusters (pgvector cosine).
"""
