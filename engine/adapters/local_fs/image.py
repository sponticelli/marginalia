"""Local-filesystem image adapter (design §8 dispatch).

Single API call per image: send the bytes as an image content block,
receive a structured plain-text description that downstream agents
treat as the canonical source content.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING

from engine.adapters._template.contract import ExtractedContent
from engine.prompts import Prompt, load_prompt
from engine.utils.api_compat import temperature_kwargs
from engine.utils.cost_tracker import estimate_cost_usd

if TYPE_CHECKING:
    from anthropic import Anthropic

PROMPT_NAME = "image_describe"
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 1024

_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class UnsupportedImageError(ValueError):
    """Raised for image extensions outside Anthropic's supported set."""


def _media_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        return _MEDIA_TYPES[suffix]
    except KeyError as exc:
        raise UnsupportedImageError(
            f"unsupported image extension {suffix!r}; expected one of {sorted(_MEDIA_TYPES)}"
        ) from exc


def extract_image(
    path: Path | str,
    *,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    prompt: Prompt | None = None,
) -> ExtractedContent:
    """Describe an image via Anthropic vision; return as ``ExtractedContent``."""
    image_path = Path(path)
    media_type = _media_type_for(image_path)
    image_bytes = image_path.read_bytes()

    if client is None:
        from anthropic import Anthropic as _Anthropic

        client = _Anthropic()

    prompt = prompt or load_prompt(PROMPT_NAME)
    user_text = prompt.user_template.format(kind=f"{media_type} image")

    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        **temperature_kwargs(model),
        system=prompt.system,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.standard_b64encode(image_bytes).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": user_text},
                ],
            }
        ],
    )

    text = resp.content[0].text
    cost = estimate_cost_usd(model, resp.usage.input_tokens, resp.usage.output_tokens)
    return ExtractedContent(
        text=text,
        pages=None,
        extraction_method="vision_image",
        chars_per_page=None,
        cost_usd=cost,
    )


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "PROMPT_NAME",
    "UnsupportedImageError",
    "extract_image",
]
