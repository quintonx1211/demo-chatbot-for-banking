# Tests

Everything here is run from the repo root (`python tests/<name>.py`), not
from inside this folder - each script inserts the repo root onto `sys.path`
itself, so `import app` resolves regardless of where it's invoked from.

None of this needs an LLM provider key to run. Scripts that *can* use one
(`eval_retrieval.py --rerank`, `eval_router.py --llm`, `compare_grounding.py`,
`calibrate_grounding.py`) degrade gracefully and say so plainly when no
provider is configured - they never present a no-model run as though the
model had answered.

## Automated

| File | What it checks | Run |
|---|---|---|
| `smoke_test.py` | End-to-end: all seven routing branches, no key needed. | `python tests/smoke_test.py` |
| `provider_test.py` | Adapter contract layer (dispatch, refusal handling, fallback, grounding gate) via stub adapters - not a real API call. | `python tests/provider_test.py` |
| `eval_retrieval.py` | Labelled retrieval quality: P@1, Recall@3, MRR, rejection rate, on 70 labelled questions. | `python tests/eval_retrieval.py [-v] [--rerank]` |
| `eval_router.py` | Lexical intent classifier vs. LLM routing, on a hand-labelled set - decides whether `router_mode` should ever leave `"nlu"`. | `python tests/eval_router.py [--llm]` |

## Diagnostic / scratch (not asserting pass/fail - print evidence for a human to read)

| File | What it's for | Run |
|---|---|---|
| `diagnose_provider.py` | Why a provider call is failing, without printing the key. | `python tests/diagnose_provider.py` |
| `compare_grounding.py` | Same question, same model, with and without grounding, side by side - the live argument for why grounding matters. | `python tests/compare_grounding.py ["question"]` |
| `calibrate_grounding.py` | Re-measures `guardrails.MIN_GROUNDING` against whichever provider is active - the threshold is tuned per model's paraphrasing style, not a universal constant. | `python tests/calibrate_grounding.py` |
| `sweep_retrieval.py` | Compares retrieval ranking strategies against the live index before any of them gets adopted in `retriever.py`. | `python tests/sweep_retrieval.py` |

## Manual

`TEST-SCENARIOS.txt` - the manual test script, structured by case-study
capability (A: deterministic NLU routing, B: RAG grounding, C: context-
preserving handoff, D: LLM handover summaries), plus demo customer
credentials and a list of known-failing questions kept in the open rather
than hidden.
