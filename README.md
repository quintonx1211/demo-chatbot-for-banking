# Bank Customer Service AI Chatbot - hybrid architecture demo

A small, runnable demo of the architecture from the case study: an **NLU +
rule layer** that routes confident requests into deterministic scripted flows,
a **RAG-grounded LLM layer** for everything else, and **automatic handoff to a
human agent** with an LLM-written brief when the system isn't confident enough
to answer.

The point of the demo is the *routing*, not the chat. Every turn shows which
layer produced the answer, why, and what it was grounded in.

## Run it

```bash
# No dependencies required - the whole demo runs on a bare Python install.
python server.py          # → http://127.0.0.1:8000
```

Without a provider the demo still runs end to end: the LLM layer degrades to an
**extractive** mode that returns verified knowledge-base text verbatim instead
of a generated answer. The header pill shows which mode is active.

To turn the generative layer on, install **one** SDK and set its key:

```bash
pip install anthropic     &&  export ANTHROPIC_API_KEY=sk-ant-...   # LLM_PROVIDER=anthropic
pip install openai        &&  export OPENAI_API_KEY=sk-...          # LLM_PROVIDER=openai
pip install google-genai  &&  export GEMINI_API_KEY=...             # LLM_PROVIDER=gemini
```

PowerShell uses `$env:ANTHROPIC_API_KEY="sk-ant-..."`. See `.env.example` for
every knob. `LLM_PROVIDER=auto` (the default) picks the first provider that is
both installed and keyed.

```bash
python smoke_test.py            # all seven routing branches, no key needed
python provider_test.py         # adapter contract, using stub adapters
python calibrate_grounding.py   # re-measure the grounding threshold per provider
```

## What to try

Open the **Customer chat** tab. Each suggestion below hits a different branch:

| Try this | What happens |
|---|---|
| `Check my balance` → `4471` | High-confidence intent → identity verification → deterministic flow reading the mock core-banking record |
| `I lost my debit card` → `4471` → `8891` → `yes` | Scripted flow with slot filling and an explicit confirmation before an irreversible action |
| `What's the status of my loan application?` → `9032` | Deterministic lookup, no model involved |
| `What are your fees for international transfers?` | No scripted flow matches → retrieval over the knowledge base → LLM answers strictly from the retrieved passages, with sources shown |
| `Should I invest my savings in tech stocks?` | Compliance guardrail - refused before any model call |
| `Do you offer crop insurance for vineyards?` | No supporting passage exists → escalation rather than a guess |
| `Let me talk to a real person` | Explicit handoff |

Then open the **Agent console** tab to see the escalated conversation, the
handover brief, the full transcript, and the per-turn audit trail.

## Managing the knowledge base

The **Knowledge base** tab lists every document, shows the passages the
retriever extracted from it, and lets you add, edit and delete documents while
the server runs. Uploads are indexed immediately - no restart.

The most instructive thing to try is the round trip:

1. Add a document with a `## Trip cancellation cover` section
2. Ask the assistant about trip cancellation → answered, citing the new passage
3. Delete the document
4. Ask again → escalates with "No supporting knowledge-base passage found"

That is the grounding principle made visible: the assistant's knowledge is
exactly the corpus, and removing a source removes the answer rather than
falling back on what the model happens to know.

**What the review pane is for.** It shows the parsed passages, not the raw
file, because passages are what retrieval actually sees. A document whose
sections didn't split the way you expected retrieves badly, and this is where
that shows up. Uploads are rejected outright if they contain no `## Section`
heading - such a file would sit in the directory looking installed while
contributing nothing.

Validation on upload: filename restricted to an allowlist (letters, digits,
dot, dash, underscore, `.md`), rejected if it resolves outside the knowledge-base
directory or collides with a Windows device name; 256 KB per document, 200
documents, 1 MB request body.

## Choosing a provider from the UI

The **Settings** tab lists the three adapters with, for each, whether the SDK is
installed and whether a key is set, and lets you switch provider, paste a key,
override the model, set effort, and point the OpenAI adapter at a compatible
gateway. Changes apply immediately to the running process.

Keys are handled as follows, and the limits matter:

- Never written to disk, and no endpoint returns one. The catalogue exposes a
  mask (`sk-a…4f2c`) and nothing else.
- Held in the process environment, so still plaintext to anything that can read
  the process - other code in it, a core dump, a debugger, a child process.
- The server binds to localhost and has no authentication.

Fine for a demo; not how to handle credentials in production, where they belong
in a secrets manager read at start-up with no UI path to set them. The Settings
tab says so on screen rather than only here.

## How a turn is routed

```
customer message
      │
      ├─ 1. pending flow?  ────────────►  deterministic  (slot answers like "4471" or "yes"
      │                                                    carry no intent signal, so an
      │                                                    in-progress flow owns the turn)
      ├─ 2. restricted topic? ─────────►  guardrail      (investment / tax / legal - refused
      │                                                    before any model sees the text)
      ├─ 3. intent confidence ≥ 0.55? ─►  deterministic  (scripted flow, templated response,
      │                                                    data read from the record)
      ├─ 4. retrieval finds evidence? ─►  RAG + LLM      (answer generated strictly over the
      │                                                    retrieved passages, then grounding-
      │                                                    checked on the way out)
      └─ 5. otherwise ─────────────────►  escalation     (+ LLM-written handover brief)
```

Escalation triggers, all of which are recorded in the audit trail:

- the customer asks for a human
- identity verification fails three times
- no knowledge-base passage covers the question
- the generated answer fails the grounding check
- intent confidence stays below threshold on two consecutive turns

## Design decisions worth calling out

**Anything touching an account is deterministic.** Balances, transactions, loan
status and card blocks are assembled from templates over the system of record.
No model output reaches the customer on those paths, which is what makes them
reproducible and auditable. `flows.py` contains no LLM call at all.

**The LLM is a rewriter, not a source.** The answering prompt supplies the
retrieved passages and forbids any claim not present in them. If retrieval
finds nothing, the turn escalates instead of being generated - the case for
grounding is precisely that the model must not fill the gap.

**Grounding is checked on the way out, not just prompted for.** After
generation, `guardrails.grounding_score` measures the share of the answer's
content words present in the retrieved context. Below 0.55 the answer is
discarded and the conversation escalates. This is a cheap proxy - it catches an
answer invented wholesale, not a subtly altered figure - and in production it
would be paired with a model-based faithfulness check. It is here to show
where that gate belongs in the pipeline.

**Retrieval is gated on coverage, not cosine similarity.** Cosine alone is a
poor relevance gate on long passages: a real match scores low in absolute
terms while an off-topic question still scores non-zero off incidental words.
`textmodel.coverages` instead asks what share of the question's information a
passage addresses, weighting unseen terms at the ceiling. On this corpus
in-scope questions score 0.29–1.00 against the right passage and out-of-scope
questions top out at 0.23; the threshold sits in that gap.

**The handover brief is written at escalation time, not on agent pickup**, so
it is already waiting when the agent opens the queue.

**PII is redacted before anything is written to the audit log** - card numbers,
SSNs, emails and phone numbers (`guardrails.redact`).

## Layout

```
server.py            stdlib HTTP server + JSON API
smoke_test.py        end-to-end test of all seven routing branches
provider_test.py     adapter contract test (stub adapters, no SDK needed)
calibrate_grounding.py   re-measure the grounding threshold per provider
app/
  router.py          the orchestrator - routing, escalation, audit
  nlu.py             intent classifier with confidence bands and regex anchors
  flows.py           deterministic scripted flows (no LLM)
  retriever.py       knowledge-base loader + coverage-gated retrieval
  kbstore.py         document upload / delete / review, with validation
  llm/               provider-agnostic generative layer (see above)
    runtime.py       runtime provider + masked key configuration
  guardrails.py      restricted topics, PII redaction, grounding check
  session.py         transcript, verification state, audit trail
  textmodel.py       dependency-free TF-IDF / coverage scoring
data/kb/*.md         the bank's verified knowledge base (4 docs, 21 passages)
data/accounts.json   mock core-banking records for two customers
web/                 single-page customer chat + agent console
```

## Swapping the LLM provider

Anthropic, OpenAI, Gemini and Groq are all supported, selected by
`LLM_PROVIDER` or from the Settings tab. Nothing outside `app/llm/` knows a
vendor name - the router calls two functions and receives an `LLMResult`.

```
app/llm/
  __init__.py               provider selection + public surface
  base.py                   prompts, LLMRequest/LLMResult, extractive fallback
  runtime.py                runtime configuration for the console
  providers/
    anthropic_provider.py
    openai_provider.py
    gemini_provider.py
    groq_provider.py        OpenAI wire format, Groq host, own key and default
```

An adapter is ~60 lines: `available()`, `model_name()`, and `complete(request,
effort) -> LLMResult`. Adding a fourth provider means one file plus one entry in
`PROVIDERS`.

**What genuinely differs between them**, and why each adapter owns its own
mapping rather than sharing one:

| | Anthropic | OpenAI | Gemini | Groq |
|---|---|---|---|---|
| System prompt | `system=[{...}]` block | `role: "system"` message | `system_instruction` on the config | `role: "system"` message |
| Reasoning depth | `output_config.effort` | `reasoning_effort` (reasoning models only - opt in via `LLM_REASONING_EFFORT`) | `thinking_config.thinking_budget`, a token count not a level | not offered - `LLM_EFFORT` is accepted and ignored |
| Prompt caching | explicit `cache_control: ephemeral` | automatic, prefix-based | separate cache object with a TTL - not wired up here | automatic |
| Token ceiling | `max_tokens` | `max_completion_tokens` | `max_output_tokens` | `max_completion_tokens` |
| Usage fields | `input_tokens` | `prompt_tokens` | `prompt_token_count` | `prompt_tokens` |
| Safety decline | `stop_reason == "refusal"` | `message.refusal` populated | `prompt_feedback.block_reason` **or** `finish_reason == SAFETY` | **no field** - arrives as ordinary text |

The last row is the one that matters architecturally. Three of the four report
a decline as a **successful HTTP response** in three different shapes, and
Gemini can block at two different points; each adapter normalises its own onto
the single `model_refusal` signal the router escalates on, because a vendor
classifier objecting to a customer's question is something a human should see,
not something to paper over with fallback text.

Groq is the exception, and it is a real gap rather than a simplification: with
no `refusal` field, a safety decline is indistinguishable from an answer at
the adapter layer, so it reaches the grounding gate as ordinary text and is
caught only if it fails that. Acceptable for a demo; worth knowing before
putting a gateway in front of customers.

## Running the demo for free

Checked August 2026. Free tiers change often - verify before relying on any of
this.

**Groq is the practical choice**, and is a first-class provider here - pick it
in the Settings tab, paste a key, done. The free limits are comfortable for a
demo (Llama 3.3 70B: 30 req/min, 1,000 req/day, 12K tokens/min):

```bash
pip install openai            # Groq speaks the OpenAI wire format
export LLM_PROVIDER=groq
export GROQ_API_KEY=gsk_...
python server.py
```

No base URL or model to set - the adapter fixes the host and defaults to
`llama-3.3-70b-versatile`. The startup banner and `/api/health` report the
endpoint, so it is never mistaken for OpenAI itself.

**Other options, ranked by how well they fit this demo:**

| Option | What you get free | Catch |
|---|---|---|
| **Groq** | 30 RPM / 1K RPD on Llama 3.3 70B and GPT-OSS | Open-weight models only; no `refusal` field, so a safety block arrives as ordinary text |
| **Google Gemini** | Flash-tier models, no card needed | **Prompts are used to train Google's products** on the free tier; Pro is paid-only, which is why the adapter defaults to Flash |
| **OpenRouter** | `:free` model variants - 50 req/day, 20 req/min | Rises to 1,000/day only after a lifetime $10 purchase |
| **Anthropic** | ~$5 trial credit on signup, phone verification | Widely reported but not stated in official docs; treat as unconfirmed |
| **OpenAI** | A "Free" tier row exists in the usage-tier table | Per-model support isn't documented and third-party reports say the common models show "not supported". The reliable entry point is a $5 top-up |

Two things worth weighing before picking one:

**Gemini's free tier trains on your prompts.** Harmless here - the knowledge
base and customer records in this repo are invented - but it rules the free
tier out the moment real documents or real customer text are involved. The paid
tier does not.

**Third-party gateways don't implement OpenAI's `refusal` field.** The adapter
maps that field to the `model_refusal` escalation; a gateway that omits it
returns a safety block as ordinary assistant text instead, so the grounding
gate becomes the only thing standing between it and the customer. Fine for a
demo, not for production.

For a bank, the honest framing is that free tiers are for *exercising the
routing*, not for evaluating answer quality - and the grounding threshold needs
re-measuring per model regardless (below).

## Re-calibrate after switching provider

`guardrails.MIN_GROUNDING = 0.55` was measured against Claude's output style. A
model that paraphrases more loosely scores lower on the same correct answer and
will escalate turns it should have answered; one that stays closer to the
source makes the gate too permissive. Run:

```bash
python calibrate_grounding.py
```

It scores real answers from the active provider against the retrieved context,
prints the separation between correct and hallucinated populations, and
suggests a threshold. If the populations overlap it says so - that means no
single threshold works for that provider and the gate needs a model-based
faithfulness check instead.

## What this demo is not

State is in memory and disappears on restart; the "core banking system" is a
JSON file; identity verification is a 4-digit check standing in for real
step-up auth; there is no authentication on the agent console; and the
knowledge base is four documents rather than a real content pipeline. Retrieval
uses TF-IDF rather than embeddings, which is adequate at this corpus size and
keeps the demo dependency-free, but a production build would use a vector index
with a reranker.

The UI is styled with quinton's palette (`#1D99D6` / `#0056B3` / `#849CCD`),
taken from a brand aggregator rather than an official brand guideline. The three
values sit at the top of `web/styles.css`; everything else derives from them, so
swap them if you have the real spec.

**No provider adapter has been verified against a successful API response.**
The adapter *layer* is covered by `provider_test.py` - dispatch, refusal
handling, fallback, the grounding gate - using stub adapters. Beyond that, each
adapter was run with a deliberately invalid key:

- **Anthropic, OpenAI, Groq** all reach their vendor's API and come back with a
  **401** - not a 400 about a parameter, and not a local `TypeError`. That
  confirms the SDK accepted every argument and the request routed to the right
  host (Groq's 401 carries Groq's own error envelope, so the base URL is
  working). It does **not** confirm the model ids or the response parsing: a
  401 is answered before the request body is validated, and parsing needs a
  real 200. Groq's default model id was instead checked against Groq's model
  list.
- **Gemini**: `google-genai` could not be installed here, so that adapter is
  written from documentation and has never executed.

Before relying on any of them, run one real request and check three things: the
default model id still exists, the response field names match, and a refusal
maps to `model_refusal`.
