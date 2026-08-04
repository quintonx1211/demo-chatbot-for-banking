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
python eval_retrieval.py -v     # labelled retrieval metrics (57 labelled questions)
python make_fixtures.py         # regenerate the .docx test document
python calibrate_grounding.py   # re-measure the grounding threshold per provider
```

The **Agent console**, **Knowledge base** and **Settings** tabs need a staff
sign-in: `agent` or `admin`, password `demo1234`. The customer chat does not.

## Two screens

**Customer** is the default — no sign-in, no tabs, just the chat and the routing
inspector. **Staff sign-in** (top right, `agent` / `demo1234`) replaces it with
the contact-centre console: dashboard, escalation queue, grounding check,
safeguards, knowledge base and settings.

The split is deliberate. Identity is established *inside* the customer
conversation only when an action needs it; everything on the staff side holds
other customers' transcripts or decides what the assistant may claim, so it is
gated server-side — `_require_staff()` runs in the handler, not in the UI.

## The dashboard, and what its numbers mean

Press **Seed demo traffic** to replay ~35 scripted conversations through the
real router. Only the *customer messages* are scripted — every routing decision,
retrieval, guardrail block and escalation is produced by the same code path a
live customer hits. The conversations are fabricated; the metrics over them are
not, and every row in "Recent conversations" opens the transcript behind it.

One figure is a model rather than a measurement, and is labelled as such on
screen: **agent time saved** multiplies deflected conversations by an assumed
4 minutes of handling time. Everything else is counted. Presenting a modelled
number as a measured one is where a demo loses the room under questioning.

## Grounding check — the argument, run live

The **Grounding check** panel asks the same question twice, of the same model at
the same settings. One call gets the retrieved passages and is told to use
nothing else; the other gets nothing. The ungrounded answer is scored for how
much of it the corpus actually supports.

This is the whole case for the architecture, demonstrated instead of asserted.
It is not rigged: the ungrounded model sometimes declines to guess rather than
inventing, and that outcome is shown as-is.

## Raw mode — the whole architecture, on or off, in the same window

The **Full architecture / Raw LLM mode** switch above the customer chat is a
second version of the grounding-check argument, run live in the actual
conversation rather than in a side panel. Flip it on and every gate the
router normally applies — the compliance guardrail, intent classification and
scripted flows, retrieval, PII redaction, the grounding check — is skipped for
that conversation. What is left is a plain LLM call over the message and the
conversation history, nothing else: `Router._raw_turn` in `app/router.py`.

Ask the same question with the switch on and off and the contrast is the
pitch: investment-advice questions get answered instead of refused, an
account balance gets guessed instead of read from the record (or the model
declines, correctly, because it has no account data - also worth showing),
and knowledge questions get answered with no citation and no check that
anything said is true. The routing inspector's "Answer source" row and the
audit trail both say plainly that nothing was checked, so the failure mode is
visible, not just asserted.

Scoped to one session (`POST /api/session/raw-mode`) and off by default. The
switch bypasses every guardrail, so it is gated exactly like the rest of the
console: `_require_staff()` runs in the handler, and the UI only renders the
control once signed in - sign in as `agent` / `demo1234`, then use the
**Customer chat** button next to Sign out to reach the chat screen without
losing the console. It changes nothing about what any other customer's
conversation is doing, live or in memory - only the one session named in the
request.

## What to try

Open the **Customer chat** tab. Each suggestion below hits a different branch:

| Try this | What happens |
|---|---|
| `Check my balance` → `4471 0512` | High-confidence intent → identity verification (phone + national ID) → deterministic flow reading the mock core-banking record |
| `I lost my debit card` → `4471 0512` → `8891` → `yes` | Scripted flow with slot filling and an explicit confirmation before an irreversible action |
| `What's the status of my loan application?` → `9032 8847` | Deterministic lookup, no model involved |
| `What are your fees for international transfers?` | No scripted flow matches → retrieval over the knowledge base → LLM answers strictly from the retrieved passages, with sources shown |
| `Should I invest my savings in tech stocks?` | Compliance guardrail - refused before any model call |
| `Do you offer crop insurance for vineyards?` | No supporting passage exists → escalation rather than a guess |
| `Let me talk to a real person` | Explicit handoff |

Then open the **Agent console** tab to see the escalated conversation, the
handover brief, the full transcript, and the per-turn audit trail.

## Retrieval quality

Retrieval changes are argued from numbers, not asserted. `eval_retrieval.py`
holds 41 questions labelled with the passage that should answer them, plus 7
the corpus genuinely does not cover:

```bash
python eval_retrieval.py -v          # lexical pipeline
python eval_retrieval.py --rerank    # adds the LLM stage (needs a provider)
```

Measured on the shipped corpus (51 passages, 8 documents):

| | P@1 | Recall@3 | MRR | Rejection |
|---|---|---|---|---|
| Lexical pipeline | 49.1% | 71.9% | 0.591 | 100% |

**These numbers got worse when the corpus got harder, and that is the point.**
On the original five hand-written documents the same pipeline scored 73.2% /
80.5% / 0.760. Adding two realistic ones — a Regulation DD fee schedule and a
complaints-and-rights document, both dense with tables whose qualifying
conditions sit paragraphs away — dropped it by 24 points of P@1. A corpus
written to be retrievable flatters a retriever; a corpus written the way banks
actually write does not.

### Where the ceiling actually is

Recall does not improve with a wider candidate pool — it is flat from N=3 to
N=20:

| Candidates | Standalone gate (0.28) | Reranking gate (0.10) |
|---|---|---|
| top-3 | 71.9% | 82.5% |
| top-10 | 71.9% | 84.2% |
| top-20 | 71.9% | 84.2% |

A flat curve means the binding constraint is the **gate**, not the ranking: the
right passage is being rejected before anything gets a chance to rank it. No
amount of reranking recovers a passage that was never retrieved.

That is also the quantified case for the reranking path. Loosening the gate to
0.10 puts the answer in the candidate pool for 84.2% of questions — 12 points
above what the standalone pipeline reaches at top-3, and 35 points above its
P@1. Whether the model converts that headroom is unmeasured: see below.

**Rejection is measured as a first-class metric.** A retriever tuned only for
recall will surface a loosely-related passage for a question the corpus does not
cover, and here that becomes a grounded-*looking* answer to something the bank
never documented. Ranking and rejection are therefore separate mechanisms:
coverage decides whether there is anything worth returning, and only then does
anything decide the order. A ranker always produces an order, even over noise,
so it can never be what makes that call.

### Two findings worth stating plainly

**BM25 and rank fusion did not beat the single-signal baseline.** Both are
implemented (`textmodel.bm25_scores`, `retriever.reciprocal_rank_fusion`) and
were measured against it: RRF over coverage + cosine + BM25 scored slightly
better at P@1 and slightly worse at Recall@3 — one or two questions out of 41,
which is noise at this sample size. At the pool size that matters for reranking
(N=10) both reach 90.2% and are indistinguishable. So the fused score is
computed and shown in the inspector, but coverage still decides the order. The
sweep that produced this is `sweep_retrieval.py`.

**Lexical retrieval has a hard ceiling here, and no threshold fixes it.** Four
labelled questions fail because the words genuinely do not overlap — "salary
payment bounces" against a document that says "returned payments" and "payroll",
"gone inactive" against "dormant". Their coverage (0.16–0.25) and BM25 (2.3–3.7)
scores sit *inside* the range occupied by out-of-scope questions, so no cut-off
separates them. Admitting them means admitting "can I buy cryptocurrency in the
app" too.

That is what the reranking stage exists for, and why it is a semantic judge
rather than another lexical signal.

### LLM reranking

Off by default; `LLM_RERANK=1` enables it. When on, the pipeline changes shape:
stage one runs for *recall* (looser gate 0.10, pool of 10) and the model scores
each candidate 0–10 for whether it answers the question. Candidates below 5 are
dropped, and if none survive the turn escalates — the judge replaces the lexical
threshold as the rejection gate rather than sitting behind it.

Every failure path falls back to lexical order and records which one happened in
the audit note (`rerank:skipped-no-provider`, `rerank:unparseable`,
`rerank:rejected-all`), so the trail never implies a rerank that did not occur.

**Its effect is unmeasured** — no API key was available in the environment this
was built in. The headroom is quantified above (84.2% of answers reach the
candidate pool); whether the model picks them out of ten is the open question.
Run `python eval_retrieval.py --rerank` with a provider configured to settle it.

## Managing the knowledge base

The **Knowledge base** tab lists every document, shows the passages the
retriever extracted from it, and lets you add, edit and delete documents while
the server runs. Uploads are indexed immediately - no restart.

**Accepted formats: `.md`, `.txt`, `.docx`** — all handled with the standard
library, no dependencies. A `.docx` is a ZIP of XML, so `app/loaders.py` unzips
it and walks the WordprocessingML tree, mapping Word's heading styles to
markdown levels and rendering tables as markdown tables. Tables are worth the
effort rather than flattening to prose: in bank documentation the numbers that
answer a question — fee, limit, timeframe — live in tables, and a row stripped
of its header is a number with no meaning.

PDF is deliberately not supported. Extracting text from PDF means
reimplementing font encodings and content-stream parsing; that is a library's
job, and it would have been the only dependency in the project.

Documents are chunked by `app/chunker.py` in three passes — headings (keeping
the full path for citation), then paragraphs, then size-bounded packing with
overlap at the seams. A section that already fits stays one chunk, so the
original hand-written corpus chunks exactly as it did before.

Generate the test fixture with `python make_fixtures.py`: a Word document with
three heading levels, a fee table, and sections worded nothing like a customer
would ask. That last part is the point — it is where a heading-only splitter and
a single lexical signal start to fail.

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

## Staff sign-in

The customer chat needs no sign-in — it is a widget on a public page, and
identity is established *inside* the conversation only when an action needs it
(`flows.py` asks for the last 4 digits before any account action). The agent
console, knowledge base and settings are the opposite: they hold other
customers' transcripts, decide what the assistant may claim, and accept an API
key. Those are gated.

Demo accounts: `agent` or `admin`, password `demo1234` — stated openly because
a credential in source is only defensible when it protects nothing real.

**The rule the implementation follows: authorise at the API, not in the UI.**
Every staff endpoint calls `_require_staff()` before doing any work. Hiding a
tab in JavaScript is not a control — those routes answer anyone who calls them
directly, so the check has to live in the handler. The sign-in panel is a
convenience on top of that, not the mechanism.

Knowledge-base write access is gated for a reason worth separating from the
others: it is an **answer-integrity** control, not housekeeping. Anyone who can
upload a document can make the assistant state anything — with a citation.

Demo-grade, deliberately: accounts are PBKDF2-hashed in source, sessions live in
memory and clear on restart, and the cookie is `HttpOnly` + `SameSite=Lax` but
not `Secure`, because this runs over plain HTTP on localhost. A real deployment
authenticates against the bank's IdP over OIDC — staff already have a corporate
identity, and building a second one to manage and leak is the wrong move.

### Agent replies

An escalated conversation is picked up from the queue and replied to in the
agent console; the customer's page polls and the reply appears in their chat,
attributed by name. Two consequences worth calling out:

**The assistant stands down.** Once a human replies, `session.handled_by` is
set and the router stops answering that conversation — the customer's later
messages are recorded for the agent but generate nothing. Two voices replying
to the same person is worse than one slower voice, and a bot talking over an
agent mid-sentence is the failure people remember.

**The audit trail records the human.** Agent turns carry `route: "agent"` and
an `actor` naming the staff member. Up to that point the trail explains how the
*assistant* decided; the moment people start replying, "who said this to the
customer" is the question a reviewer is actually asking, and a trail that only
covers the bot answers half of it.

The customer's poll endpoint is public and scoped by session id, so it returns
messages only — never the audit trail, the escalation reason, or the customer
record. The session id is a bearer credential in that model, which is why it is
now 24 bytes of `secrets.token_urlsafe` rather than the 32-bit truncated uuid it
started as.

Polling rather than websockets: a few lines against a stdlib server, and the
latency does not matter at this cadence.

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
      ├─ 1. pending flow?  ────────────►  deterministic  (slot answers like "4471 0512" or "yes"
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

**Identity verification is two independent factors, checked together.** Phone
last-4 alone was a single 4-digit secret with no lockout wider than one
session - a fresh session resets the 3-attempt counter, so it does not stop
guessing, only slow it per session. Adding the national ID (CCCD) last-4 as a
second factor, matched together against the same customer record, raises the
search space from "guess one code" to "guess two codes for the same person at
once" - the cheapest real improvement available without a true step-up
channel. The failure message is deliberately generic ("those details don't
match") rather than naming which factor was wrong, so a correct phone number
can't be fished out one guess at a time. See `app/flows.py`
`_verification_prompt` / `_handle_verification`.

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
eval_retrieval.py    labelled retrieval metrics (P@1, recall, MRR, rejection)
sweep_retrieval.py   compares ranking strategies before one is adopted
make_fixtures.py     generates the .docx test document
calibrate_grounding.py   re-measure the grounding threshold per provider
app/
  router.py          the orchestrator - routing, escalation, audit
  nlu.py             intent classifier with confidence bands and regex anchors
  flows.py           deterministic scripted flows (no LLM)
  retriever.py       knowledge-base loader + coverage-gated retrieval
  chunker.py         heading / paragraph / size-bounded chunking with overlap
  loaders.py         .md / .txt / .docx extraction (stdlib only)
  kbstore.py         document upload / delete / review, with validation
  auth.py            staff sessions for the privileged endpoints
  llm/               provider-agnostic generative layer (see above)
    runtime.py       runtime provider + masked key configuration
    rerank.py        optional LLM reranking / semantic rejection
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
$env:GROQ_API_KEY = ""
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
JSON file; identity verification is two 4-digit checks (phone + national ID)
standing in for real step-up auth - not an OTP to a registered device, not a
live check against a telco or government ID registry, and still brute-forceable
across sessions since the 3-attempt lockout does not persist past one; there is
no authentication on the agent console; and the
knowledge base is four documents rather than a real content pipeline. Retrieval
uses TF-IDF rather than embeddings, which is adequate at this corpus size and
keeps the demo dependency-free, but a production build would use a vector index
with a reranker.

The UI carries no branding. Three colour values at the top of `web/styles.css`
drive the whole palette, so re-theming means changing those and nothing else.

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

## Internet search as a fourth routing tier — assessed, not built

The client asked us to consider adding a web-search tier between RAG and the
human queue. We looked at it and recommend against it for this product. The
reasoning, so the decision can be revisited rather than re-argued:

**What it would fix.** Roughly a third of current escalations are questions the
bank *can* answer but has not written down yet — the unresolved-topic panel
shows exactly which. Web search would not fix those either; the answer is in the
bank's own procedures, not on the open web. Writing the missing page fixes them
permanently and costs nothing per query.

**What it would break.** The single property that makes this assistant safe to
put in front of a bank's customers is that every answer is traceable to a
document the bank approved. A web result is not that. The moment the assistant
can cite a third-party page, "the bank told me X" becomes true for content the
bank never reviewed — and for a regulated institution, that is a compliance
finding, not a feature. The grounding check cannot save us here: it measures
whether the answer follows from its sources, not whether the sources should have
been trusted.

**Where it does belong.** Two narrow cases, both out of scope for this phase and
both worth revisiting: (a) public reference data with a named authority — a
central-bank FX rate, a public-holiday calendar — where the source is a specific
allow-listed endpoint, not "the web"; (b) internal search over the bank's own
public site, which is really just a larger corpus for the existing RAG tier and
needs no new architecture at all.

**Recommendation.** Do not add an open-web tier. Add (b) — index the bank's
public site into the same corpus — and keep the escalation path for everything
the bank has not published. The unresolved-topic clustering is what turns those
escalations into a content backlog, which is the durable fix.
