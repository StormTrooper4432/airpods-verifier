"""Stage 1 of the pipeline: photographs in, structured observations out.

This module is the *only* place an LLM is used, and it is deliberately fenced
in. Claude is asked to read the box and describe what it sees; it is never
asked whether the item is genuine. The response shape is enforced server-side
by structured outputs against ``VisionExtraction``'s JSON Schema, so the
boundary between "extraction" and "judgement" is enforced by the API, not by
hoping the prompt holds.

The system prompt is read from PROMPTS.md at call time, keeping the prompt
engineering in a reviewable document rather than buried in a string literal.

Cost notes (the dominant cost is images, not text):
  * Image tokens are roughly ``(width x height) / 750``. A 12 MP phone photo is
    ~4,000 tokens; the same photo resized to a 1,568 px long edge is ~1,900. We
    downscale locally when Pillow is installed, which also normalises HEIC and
    other phone formats into a media type the API accepts.
  * All photographs of one item go in a *single* request. Beyond being cheaper
    than one call per photo, it lets the model cross-reference the box against
    the hardware (e.g. reading the A-number off the case when the box is worn).
"""

from __future__ import annotations

import base64
import io
import os
import re
from dataclasses import dataclass
from pathlib import Path

import anthropic

from schema import VisionExtraction

try:  # Optional: local downscaling / format normalisation.
    from PIL import Image

    _PILLOW_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without Pillow
    _PILLOW_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_MODEL = os.environ.get("AIRPODS_VERIFIER_MODEL", "claude-opus-5")

#: Extraction is OCR plus a handful of boolean observations, not deep
#: reasoning. "medium" keeps the fine-print reading sharp while spending
#: meaningfully fewer thinking tokens than the default of "high".
DEFAULT_EFFORT = os.environ.get("AIRPODS_VERIFIER_EFFORT", "medium")

#: The API downscales anything larger than this anyway; doing it locally means
#: we neither upload nor pay for pixels that get thrown away.
MAX_IMAGE_EDGE_PX = 1568

#: Headroom for adaptive thinking plus the (tiny) JSON payload.
MAX_TOKENS = 8192

PROMPTS_PATH = Path(__file__).with_name("PROMPTS.md")

_SYSTEM_PROMPT_HEADING = "## Vision Extractor System Prompt"

#: USD per million tokens (input, output). Rates drift - treat as an estimate.
PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

#: Cached input tokens are billed at a fraction of the normal input rate.
_CACHE_READ_DISCOUNT = 0.1

_EXTENSION_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: Flipped off permanently for the process if the account cannot use the
#: server-side fallback beta, so we degrade once rather than on every call.
_fallbacks_enabled = True


class VisionExtractionError(RuntimeError):
    """Raised when photographs could not be turned into a valid extraction."""


@dataclass(frozen=True)
class ExtractionResult:
    extraction: VisionExtraction
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    estimated_cost_usd: float | None


# --------------------------------------------------------------------------- #
# Prompt loading
# --------------------------------------------------------------------------- #


def load_system_prompt(prompts_path: Path | str = PROMPTS_PATH) -> str:
    """Read the system prompt out of PROMPTS.md.

    Takes everything under the "Vision Extractor System Prompt" heading up to
    the next top-level heading, so PROMPTS.md remains the single source of
    truth for prompt wording.
    """
    path = Path(prompts_path)
    try:
        document = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VisionExtractionError(f"Could not read the prompt file at {path}: {exc}") from exc

    if _SYSTEM_PROMPT_HEADING not in document:
        raise VisionExtractionError(
            f"{path} does not contain a '{_SYSTEM_PROMPT_HEADING}' section."
        )

    body = document.split(_SYSTEM_PROMPT_HEADING, 1)[1]
    # Stop at the next top-level heading, if there is one.
    body = re.split(r"^## ", body, maxsplit=1, flags=re.MULTILINE)[0]
    prompt = body.strip()
    if not prompt:
        raise VisionExtractionError(f"The system prompt section in {path} is empty.")
    return prompt


# --------------------------------------------------------------------------- #
# Image preparation
# --------------------------------------------------------------------------- #


def encode_image(image_path: Path | str) -> tuple[str, str]:
    """Return ``(media_type, base64_data)`` for one photograph.

    With Pillow installed every image is normalised to a downscaled JPEG, which
    handles HEIC and other phone-camera formats the API does not accept. Without
    it we fall back to the raw bytes and the extension's media type.
    """
    path = Path(image_path)
    if not path.is_file():
        raise VisionExtractionError(f"Image not found: {path}")

    if _PILLOW_AVAILABLE:
        try:
            return "image/jpeg", _to_downscaled_jpeg(path)
        except OSError as exc:
            raise VisionExtractionError(f"Could not open {path} as an image: {exc}") from exc

    media_type = _EXTENSION_MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise VisionExtractionError(
            f"Unsupported image format '{path.suffix}' for {path}. Supported without Pillow: "
            f"{', '.join(sorted(_EXTENSION_MEDIA_TYPES))}. Install Pillow to convert other formats."
        )
    return media_type, base64.standard_b64encode(path.read_bytes()).decode("utf-8")


def _to_downscaled_jpeg(path: Path) -> str:
    with Image.open(path) as image:
        image = image.convert("RGB")  # drops alpha; JPEG cannot carry it
        longest_edge = max(image.size)
        if longest_edge > MAX_IMAGE_EDGE_PX:
            scale = MAX_IMAGE_EDGE_PX / longest_edge
            new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            image = image.resize(new_size, Image.LANCZOS)

        buffer = io.BytesIO()
        # Quality 85 keeps fine print legible; the box's small text is the whole
        # point of the exercise, so this is not the place to over-compress.
        image.save(buffer, format="JPEG", quality=85, optimize=True)

    return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")


def build_user_content(image_paths: list[Path | str]) -> list[dict]:
    """Interleave labelled text with each image so the model can cite sources."""
    if not image_paths:
        raise VisionExtractionError("At least one image is required.")

    content: list[dict] = []
    for index, image_path in enumerate(image_paths, start=1):
        media_type, data = encode_image(image_path)
        content.append(
            {"type": "text", "text": f"Image {index} ({Path(image_path).name}):"}
        )
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": data},
            }
        )

    content.append(
        {
            "type": "text",
            "text": (
                f"The {len(image_paths)} image(s) above show a single AirPods Pro 2 listing. "
                "Extract the attributes defined in your instructions. Report only what is "
                "actually visible; use null wherever a value is not legible."
            ),
        }
    )
    return content


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


def extract(
    image_paths: list[Path | str],
    *,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    client: anthropic.Anthropic | None = None,
    prompts_path: Path | str = PROMPTS_PATH,
) -> ExtractionResult:
    """Send photographs to Claude and return validated observations."""
    client = client or anthropic.Anthropic()
    system_prompt = load_system_prompt(prompts_path)
    content = build_user_content(image_paths)

    request = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        # A list block (rather than a bare string) so the stable prompt carries a
        # cache breakpoint. This only engages above the model's minimum cacheable
        # prefix, and the images downstream of it are never cached - the savings
        # here are small next to image tokens.
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": content}],
        "output_format": VisionExtraction,
        "output_config": {"effort": effort},
    }

    response = _call_with_fallbacks(client, request)

    if response.stop_reason == "refusal":
        raise VisionExtractionError(
            "The model declined to process these images "
            f"(category: {getattr(response.stop_details, 'category', None)})."
        )
    if response.stop_reason == "max_tokens":
        raise VisionExtractionError(
            f"Extraction was truncated at max_tokens={MAX_TOKENS}. Retry with fewer images."
        )

    extraction = response.parsed_output
    if extraction is None:
        raise VisionExtractionError(
            "The response contained no parsable structured output. This usually means the "
            "images did not show an identifiable product."
        )

    usage = response.usage
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return ExtractionResult(
        extraction=extraction,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=cache_read,
        estimated_cost_usd=estimate_cost(model, usage.input_tokens, usage.output_tokens, cache_read),
    )


def _call_with_fallbacks(client: anthropic.Anthropic, request: dict):
    """Call the API, degrading gracefully if server-side fallbacks are unavailable.

    Server-side fallbacks reroute a refusal to another model instead of failing
    the request. Accounts without the beta enabled get a 400, so we drop the
    parameter once for the life of the process rather than on every call.
    """
    global _fallbacks_enabled

    try:
        if _fallbacks_enabled:
            return client.beta.messages.parse(
                **request, fallbacks="default", betas=[_FALLBACK_BETA]
            )
        return client.messages.parse(**request)
    except anthropic.BadRequestError as exc:
        if _fallbacks_enabled and _looks_like_fallback_rejection(exc):
            _fallbacks_enabled = False
            return client.messages.parse(**request)
        raise VisionExtractionError(f"The API rejected the request: {exc}") from exc
    except anthropic.NotFoundError as exc:
        raise VisionExtractionError(
            f"Model '{request['model']}' is not available to this account: {exc}"
        ) from exc
    except anthropic.RateLimitError as exc:
        raise VisionExtractionError(f"Rate limited by the API; retry shortly: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise VisionExtractionError(f"API error {exc.status_code}: {exc}") from exc
    except anthropic.APIConnectionError as exc:
        raise VisionExtractionError(f"Could not reach the API: {exc}") from exc


def _looks_like_fallback_rejection(exc: anthropic.BadRequestError) -> bool:
    message = str(exc).lower()
    return "fallback" in message or "beta" in message


def estimate_cost(
    model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0
) -> float | None:
    """Rough USD cost of one extraction, or None for an unpriced model."""
    rates = PRICING_PER_MTOK.get(model)
    if rates is None:
        return None
    input_rate, output_rate = rates
    billable_input = max(0, input_tokens - cache_read_tokens)
    cost = (
        billable_input * input_rate
        + cache_read_tokens * input_rate * _CACHE_READ_DISCOUNT
        + output_tokens * output_rate
    ) / 1_000_000
    return round(cost, 6)
