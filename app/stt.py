"""STT wrapper - PhoWhisper medium (Vietnamese).

vinai/PhoWhisper-medium is fine-tuned specifically for Vietnamese,
giving noticeably better accuracy than plain Whisper.
"""
from __future__ import annotations

import logging
import os
import tempfile

logger = logging.getLogger(__name__)

MODEL_ID = "vinai/PhoWhisper-medium"

_pipeline = None


def _load():
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    try:
        from transformers import pipeline as hf_pipeline
    except ImportError:
        raise RuntimeError(
            "transformers not installed – run: pip install transformers torch soundfile"
        )
    logger.info("Loading STT model: %s", MODEL_ID)
    _pipeline = hf_pipeline(
        "automatic-speech-recognition",
        model=MODEL_ID,
        chunk_length_s=30,
    )
    return _pipeline


def transcribe_wav(wav_bytes: bytes) -> str:
    return transcribe(wav_bytes, "audio/wav")


def transcribe(audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
    """Transcribe audio bytes using PhoWhisper. Accepts wav, mp4, webm, ogg…"""
    if "mp4" in mime_type or "m4a" in mime_type or "aac" in mime_type:
        ext = ".mp4"
    elif "ogg" in mime_type or "opus" in mime_type:
        ext = ".ogg"
    elif "webm" in mime_type:
        ext = ".webm"
    else:
        ext = ".wav"
    pipe = _load()
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
        f.write(audio_bytes)
        tmp = f.name
    try:
        result = pipe(tmp)
        return result["text"].strip()
    finally:
        os.unlink(tmp)


def warmup() -> bool:
    """Pre-load PhoWhisper at server startup."""
    if not available():
        return False
    try:
        _load()
        return True
    except Exception as exc:
        logger.warning("STT warmup failed: %s", exc)
        return False


def available() -> bool:
    try:
        import transformers  # noqa: F401
        return True
    except ImportError:
        return False
