"""Tải model giọng nói Piper (tiếng Việt) cho demo này.

Sử dụng:
    python setup_voice.py
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

VOICE_DIR = Path(__file__).resolve().parent.parent / "voices"

VOICE = {
    "name": "vi_VN-vivos-x_low",
    "url": (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main"
        "/vi/vi_VN/vivos/x_low"
    ),
}


def main() -> None:
    VOICE_DIR.mkdir(exist_ok=True)
    print(f"  [{VOICE['name']}]")
    for ext in (".onnx", ".onnx.json"):
        dest = VOICE_DIR / f"{VOICE['name']}{ext}"
        if dest.exists():
            print(f"    Đã có sẵn: {dest.name}")
            continue
        url = f"{VOICE['url']}/{VOICE['name']}{ext}"
        print(f"    Đang tải {dest.name}…", end=" ", flush=True)
        urllib.request.urlretrieve(url, dest)
        size_kb = dest.stat().st_size // 1024
        print(f"xong ({size_kb:,} KB)")
    print("Model giọng nói đã sẵn sàng – chạy `python3 server.py` để khởi động demo.")


if __name__ == "__main__":
    main()
