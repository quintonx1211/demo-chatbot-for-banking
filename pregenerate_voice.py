"""Pre-generate voice for every fixed, default reply.

Run once, and again any time `app/canned_responses.py` changes:

    python pregenerate_voice.py

Why this exists: /api/tts calls a live provider on every request, which is
the right thing to do for an answer that is different every time (a balance,
a name) and wasted latency for the ~30 replies in canned_responses.py that
are the exact same text every single time they are sent - the greeting, the
guardrail refusal, "xin lỗi, mình không xác minh được thông tin của bạn",
and so on. Baking those to data/voice_cache/ ahead of time means the first
customer to hear one today gets it exactly as fast as the thousandth;
without this, only the *second* customer to trigger a given reply in a
freshly started process would ever benefit from the in-memory cache in
app/tts.py.

Two ways to run it:

    python pregenerate_voice.py                        # gTTS, in-process, no server needed
    python pregenerate_voice.py --provider ElevenLabs  # ElevenLabs, in-process (needs the key)
    python pregenerate_voice.py --via-server http://127.0.0.1:8000
                                                       # through a running server, whatever
                                                       # provider/voice it is configured with

gTTS needs no API key and calls its provider directly, in this process, which
is everything a one-off batch script should not have to stand up just to speak
thirty fixed sentences. ElevenLabs is a plain request/response call, so it also
runs in-process here - pass its voice_id with --voice.

`--via-server` is the alternative: it POSTs the same `/api/tts` a browser would
to an already-running server, once per reply, and writes each response straight
into data/voice_cache/ under the same hash `synthesize()` will look for later -
see `cache_path_for` in app/tts.py. Use it when you want the cache baked with
exactly the provider/voice the live server is set to, without configuring it
twice.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from app import tts
from app.canned_responses import all_responses


def _via_process(provider: str, voice: str | None, force: bool) -> int:
    tts.configure(provider=provider, voice=voice)
    if not tts.available():
        print(
            f"Provider '{provider}' is not available right now - "
            f"check its API key or that `gtts` is installed, then try again.",
            file=sys.stderr,
        )
        return 1

    responses = all_responses()
    print(
        f"Provider: {provider}"
        + (f" (voice: {voice})" if voice else "")
        + f"\n{len(responses)} canned replies to check in {tts.VOICE_CACHE_DIR}\n"
    )

    made = skipped = failed = 0
    for name, text in sorted(responses.items()):
        path = tts.cache_path_for(text)
        if path.exists() and not force:
            skipped += 1
            print(f"  [skip]  {name}")
            continue
        try:
            audio = tts.synthesize_live(text)
        except Exception as exc:
            failed += 1
            print(f"  [FAIL]  {name}: {exc}", file=sys.stderr)
            continue
        path.write_bytes(audio)
        made += 1
        print(f"  [made]  {name}  ->  {path.name}  ({len(audio):,} bytes)")

    _report(made, skipped, failed)
    return 1 if failed else 0


def _via_server(base_url: str, force: bool) -> int:
    base_url = base_url.rstrip("/")
    try:
        with urllib.request.urlopen(f"{base_url}/api/tts/provider", timeout=10) as resp:
            status = json.loads(resp.read())
    except Exception as exc:
        print(
            f"Could not reach {base_url}/api/tts/provider - is `python server.py` "
            f"running? ({exc})",
            file=sys.stderr,
        )
        return 1

    provider = status.get("provider", "?")
    voice = status.get("voice")
    print(
        f"Server: {base_url}\nProvider: {provider}"
        + (f" (voice: {voice})" if voice else "")
    )

    responses = all_responses()
    print(f"{len(responses)} canned replies to check in {tts.VOICE_CACHE_DIR}\n")

    made = skipped = failed = 0
    for name, text in sorted(responses.items()):
        path = tts.cache_path_for(text, provider=provider, voice=voice)
        if path.exists() and not force:
            skipped += 1
            print(f"  [skip]  {name}")
            continue
        try:
            body = json.dumps({"text": text}).encode("utf-8")
            req = urllib.request.Request(
                f"{base_url}/api/tts",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                audio = resp.read()
        except urllib.error.HTTPError as exc:
            failed += 1
            detail = exc.read().decode("utf-8", errors="replace")
            print(f"  [FAIL]  {name}: HTTP {exc.code} {detail}", file=sys.stderr)
            continue
        except Exception as exc:
            failed += 1
            print(f"  [FAIL]  {name}: {exc}", file=sys.stderr)
            continue
        tts.VOICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
        made += 1
        print(f"  [made]  {name}  ->  {path.name}  ({len(audio):,} bytes)")

    _report(made, skipped, failed)
    return 1 if failed else 0


def _report(made: int, skipped: int, failed: int) -> None:
    print(f"\n{made} generated, {skipped} already cached, {failed} failed.")
    if failed:
        print(
            "Re-run this script once the failures above are fixed - "
            "the cache is only trustworthy once every reply has a file."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--provider",
        default="gtts",
        choices=["gtts", "ElevenLabs"],
        help="TTS provider to bake the cache with (default: gtts). "
        "Ignored with --via-server - the server's own configured "
        "provider is used instead.",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help="ElevenLabs voice_id (only used with --provider ElevenLabs, "
        "in-process mode)",
    )
    parser.add_argument(
        "--via-server",
        default=None,
        metavar="URL",
        help="POST to a running server's /api/tts instead of calling "
        "the provider in this process",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="regenerate even entries that are already cached",
    )
    args = parser.parse_args()

    tts.VOICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if args.via_server:
        return _via_server(args.via_server, args.force)
    return _via_process(args.provider, args.voice, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
