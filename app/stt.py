"""STT wrapper – PhoWhisper medium (tiếng Việt).

vinai/PhoWhisper-medium được fine-tune chuyên biệt cho tiếng Việt,
cho độ chính xác vượt trội so với Whisper thông thường.
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
    """Transcribe raw WAV bytes using PhoWhisper (tiếng Việt)."""
    pipe = _load()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
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
