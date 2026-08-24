# Voice cache

Pre-generated audio (`.mp3`) for the fixed replies in `app/canned_responses.py`
- the greeting, goodbye, guardrail refusal, verification prompts, and the
other ~30 messages that are the exact same text every single time they are
sent. `app/tts.py`'s `synthesize()` checks this folder before ever calling a
live TTS provider, so the first customer to hear "Xin chào! Mình là Linh..."
today gets it as fast as the thousandth.

Generate (or refresh, after editing `canned_responses.py`):

```bash
python pregenerate_voice.py                                       # gTTS, no server needed

python server.py                                                  # in another terminal -
                                                                    # needs VBEE_TTS_KEY/VBEE_APP_ID
python pregenerate_voice.py --provider vbee \
    --via-server http://127.0.0.1:8000                             # Vbee, through the running server
```

The default provider/voice (see `app/tts.py`) is Vbee's "Nữ Sài Gòn – Mềm
mại" (`sg_female_thaotrinh_full_44k-phg`) - a gentle Southern voice. Vbee's
TTS call only resolves through a webhook to a public URL, which is set up by
`server.py`'s own ngrok tunnel at startup - not by this script - so
`--provider vbee` requires `--via-server` pointed at a running server rather
than calling the provider directly.

Filenames are a hash of `provider|voice|text`, not the reply's name - so a
reply that changes wording never risks serving stale audio under an old
filename; it just misses the cache and falls back to a live call until this
script runs again.

Nothing dynamic belongs here (a balance, a customer's name, a reference
number) - only text that is identical on every send is worth pre-generating,
which is also the only kind of text this folder can safely serve without a
mismatch.
