# Bank Customer Service AI Chatbot

Hybrid architecture demo: NLU + rules route confident requests to scripted
flows, RAG-grounded LLM handles the rest, low-confidence turns escalate to a
human agent. Catalogue is 4 real credit cards, each mapped 1-1 to a customer
segment.

## Run it

```bash
python server.py                 # -> http://127.0.0.1:8000, no dependencies needed
```

No API key: the LLM layer runs in extractive mode. To enable generation,
install one SDK and set its key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`GEMINI_API_KEY` / `GROQ_API_KEY`), or pick a provider in the Settings tab.

Customer chat needs no sign-in. Staff console (`agent`/`admin`, password
`demo1234`) has the dashboard, escalation queue, knowledge base and settings.
