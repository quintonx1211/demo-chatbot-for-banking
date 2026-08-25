# Voice cache

Pre-generated audio (`.mp3`) for the fixed replies in `app/canned_responses.py`
- the greeting, goodbye, guardrail refusal, verification prompts, and the
other ~30 messages that are the exact same text every single time they are
sent. `app/tts.py`'s `synthesize()` checks this folder before ever calling a
live TTS provider, so the first customer to hear "Trợ lý ảo ABC Bank hân hạnh..."
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

Filenames are the constant's own name from `canned_responses.py` -
`GREETING.mp3`, `ESCALATION_MESSAGE.mp3`, and so on - not a content hash.
`app/tts.py::named_cache_path()` matches an incoming reply to its file by
looking up the *exact text* against every canned reply's text, then reading
`<CONSTANT_NAME>.mp3`; `synthesize()` checks this before anything else
(before Markdown stripping, before the hash-based fallback cache, before any
live provider call), and serves the file byte-for-byte with no processing.

A file placed here by hand - a human recording, a vendor delivery - is
therefore final: `pregenerate_voice.py` checks for `<NAME>.mp3` first and
never touches it if present, so it can't be overwritten by a lesser TTS
generation on a later run. Pass `--force` to intentionally replace one, e.g.
after re-recording it.

The tradeoff this naming makes, stated plainly: unlike a content hash, a
file named after the constant does **not** automatically go stale when the
reply's wording changes in `canned_responses.py` - it keeps matching by
name, so a wording edit silently starts serving the *old* recording's audio
for the *new* text until someone notices and re-records it. `manifest.json`
(rewritten by every `pregenerate_voice.py` run) is the way to notice: it
lists every reply's `name`, current `text`, `spoken_text` (Markdown
stripped - what a recording should actually say), `filename`, and whether
that file already exists (`recorded: true/false`) - diff it after editing a
reply's wording to see which recordings are now describing text that no
longer exists.

Nothing dynamic belongs here (a balance, a customer's name, a reference
number) - only text that is identical on every send is worth pre-generating,
which is also the only kind of text this folder can safely serve without a
mismatch.
