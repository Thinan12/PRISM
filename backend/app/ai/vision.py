"""Image table transcription via Claude vision (optional).

Only used for screenshots/photos of tables. Output is flagged in lineage as AI-transcribed;
the transcription is validated structurally (rectangular grid of strings) and never
"corrected" by the model — it is asked to copy, not infer.
"""

from __future__ import annotations

import base64
import json
import re

from app.core.config import get_settings
from app.core.errors import IngestionError
from app.ingestion.readers import ImageOCR

PROMPT = (
    "Transcribe every table in this image exactly as written. Return ONLY JSON of the form "
    '{"rows": [["cell", ...], ...]} with the header row first. Copy text verbatim, including Lao script, '
    "numbers, separators and currency symbols. Use null for empty cells. Do not compute, summarize, "
    "translate or invent any value. If a cell is illegible use \"[illegible]\"."
)
MEDIA = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"GIF8": "image/gif", b"RIFF": "image/webp"}


def make_ocr() -> ImageOCR | None:
    s = get_settings()
    if not s.anthropic_api_key:
        return None

    def ocr(data: bytes, name: str) -> list[list[str | None]]:
        import anthropic

        if len(data) > 5 * 1024 * 1024:
            raise IngestionError("Images larger than 5 MB cannot be transcribed.")
        media = next((m for sig, m in MEDIA.items() if data.startswith(sig)), "image/png")
        client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=s.ai_timeout_seconds)
        try:
            msg = client.messages.create(
                model=s.ai_model,
                max_tokens=16000,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media, "data": base64.b64encode(data).decode()}},
                        {"type": "text", "text": PROMPT},
                    ],
                }],
            )
        except anthropic.APIError as e:
            raise IngestionError(f"Image transcription failed: {e}") from e
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise IngestionError("Image transcription returned no table.")
        try:
            rows = json.loads(m.group(0))["rows"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise IngestionError("Image transcription returned malformed output.") from e
        if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
            raise IngestionError("Image transcription returned malformed output.")
        return [[None if c is None else str(c) for c in r] for r in rows]

    return ocr
