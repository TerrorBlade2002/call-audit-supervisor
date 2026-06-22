"""Eval gate (§16.2): a labelled gold set + a runner that fails CI when judge quality
regresses. The gold set is built from VERIFIER judgements and snapshotted to a committed
fixture so CI runs hermetically (no DB/R2 needed)."""
