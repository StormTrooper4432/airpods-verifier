"""Stage 1 of the pipeline: photographs in, structured observations out.

This module owns everything that is true regardless of which model reads the
photos - loading the prompt, preparing images, and enforcing that the LLM's
only output is a ``VisionExtraction``. The API call itself is delegated to a
provider in ``providers.py``.

Claude is asked to read the box and describe what it sees; it is never asked
whether the item is genuine. Both providers constrain the response to the
``VisionExtraction`` JSON Schema server-side, so the boundary between
"extraction" and "judgement" is enforced by the API, not by hoping the prompt
holds.

Cost notes (images dominate, not text):
  * Image tokens scale with pixel count. A 12 MP phone photo is thousands of
    tokens; the same photo at a 1,568 px long edge is far fewer. We downscale
    locally, which also normalises HEIC and other phone formats into a media
    type both APIs accept.
  * All photographs of one item go in a *single* request. Beyond being cheaper,
    it lets the model cross-reference the box against the hardware - reading the
    A-number off the case when the box print is worn, for instance.
"""

from __future__ import annotations

import base64
import io
import os
import re
import sys
from pathlib import Path

from font_forensics import (
    MINIMUM_GLYPH_HEIGHT_PX,
    analyse_label_lines,
    sf_available,
)
from image_quality import sharpest
from providers import (
    DEFAULT_PROVIDER,
    PreparedImage,
    ProviderError,
    ProviderResult,
    get_provider,
    is_cooling_down,
    is_rate_limited,
    is_transient_failure,
    mark_rate_limited,
)
from schema import LabelRegion, LocalAnalysis, VisionExtraction

try:  # Optional: local downscaling / format normalisation.
    from PIL import Image

    _PILLOW_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without Pillow
    _PILLOW_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

#: Extraction is OCR plus a handful of boolean observations, not deep
#: reasoning. "medium" keeps fine-print reading sharp while spending
#: meaningfully fewer reasoning tokens than the maximum.
DEFAULT_EFFORT = os.environ.get("AIRPODS_VERIFIER_EFFORT", "medium")

#: Both APIs downscale beyond roughly this size anyway; doing it locally means
#: we neither upload nor pay for pixels that get thrown away.
MAX_IMAGE_EDGE_PX = 1568

PROMPTS_PATH = Path(__file__).with_name("PROMPTS.md")

_SYSTEM_PROMPT_HEADING = "## Vision Extractor System Prompt"

_EXTENSION_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class VisionExtractionError(RuntimeError):
    """Raised when photographs could not be turned into a valid extraction."""


class ReferencePhotoError(VisionExtractionError):
    """Raised when a protected reference photograph would be sent to an API."""


#: Directories holding the owner's own genuine units. These photographs carry
#: real serial numbers, and the Gemini free tier states that submitted content is
#: used to improve Google's products - so sending one would hand a valid Apple
#: serial to a third party for retention. A cloned valid serial passes Apple's
#: coverage lookup, which is the exact fraud this project detects.
#:
#: Everything the font baseline needs is measured locally with Pillow, so these
#: never need to leave the machine. This is a hard block rather than a
#: convention: conventions get forgotten during a refactor.
PROTECTED_PATH_MARKERS = ("knowledge/real_", "knowledge\\real_")


def assert_not_reference_photo(image_path: Path | str) -> None:
    """Refuse to transmit a protected reference photograph."""
    resolved = str(Path(image_path).resolve()).replace("\\", "/")
    if any(marker.replace("\\", "/") in resolved for marker in PROTECTED_PATH_MARKERS):
        raise ReferencePhotoError(
            f"Refusing to send {Path(image_path).name} to a vision API: it is a genuine "
            f"reference photograph under knowledge/real_*, which carries a real serial number. "
            f"Reference photographs are measured locally (font_forensics, image_quality) and "
            f"never transmitted."
        )

# Provider failures are re-raised as VisionExtractionError below, so callers of
# this module only ever need to catch one exception type.


# --------------------------------------------------------------------------- #
# Prompt loading
# --------------------------------------------------------------------------- #


def load_system_prompt(prompts_path: Path | str = PROMPTS_PATH) -> str:
    """Read the system prompt out of PROMPTS.md.

    Takes everything under the "Vision Extractor System Prompt" heading up to
    the next top-level heading, so PROMPTS.md stays the single source of truth
    for prompt wording - and both providers get byte-identical instructions.
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
    also handles HEIC and other phone-camera formats neither API accepts.
    Without it we fall back to raw bytes and the extension's media type.
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


def prepare_images(image_paths: list[Path | str]) -> list[PreparedImage]:
    """Encode every photograph and label it so the model can cite its source."""
    if not image_paths:
        raise VisionExtractionError("At least one image is required.")

    prepared: list[PreparedImage] = []
    for index, image_path in enumerate(image_paths, start=1):
        assert_not_reference_photo(image_path)
        media_type, data = encode_image(image_path)
        prepared.append(
            PreparedImage(
                label=f"Image {index} ({Path(image_path).name}):",
                media_type=media_type,
                data_b64=data,
            )
        )
    return prepared


def build_instruction(image_count: int) -> str:
    return (
        f"The {image_count} image(s) above show a single AirPods Pro 2 listing. "
        "Extract the attributes defined in your instructions. Report only what is "
        "actually visible; use null wherever a value is not legible."
    )


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


def resolve_model_chain(
    vision_provider,
    model: str | None,
    fallback: bool,
    allow_lite: bool = False,
    lite_only: bool = False,
) -> list[str]:
    """Models to try, in order.

    An explicitly requested model pins the run: silently answering a
    ``--model`` request with a different model would corrupt any benchmark
    comparing two models on the same photographs.

    Models cooling down from a recent rate limit are moved to the back rather
    than dropped - if every model is cooling down we still have to try one.
    """
    if model is not None:
        return [model]
    if not fallback:
        return [vision_provider.default_model]

    if lite_only:
        attribute = "lite_only_chain"
    elif allow_lite:  # historical name: "extend beyond the default chain"
        attribute = "extended_fallback_chain"
    else:
        attribute = "fallback_chain"
    chain = list(
        getattr(vision_provider, attribute, None)
        or getattr(vision_provider, "fallback_chain", ())
        or [vision_provider.default_model]
    )
    ready = [m for m in chain if not is_cooling_down(m)]
    cooling = [m for m in chain if is_cooling_down(m)]
    return ready + cooling


def extract(
    image_paths: list[Path | str],
    *,
    provider: str | None = None,
    model: str | None = None,
    effort: str = DEFAULT_EFFORT,
    client=None,
    prompts_path: Path | str = PROMPTS_PATH,
    fallback: bool = True,
    allow_lite: bool = False,
    lite_only: bool = False,
) -> ProviderResult:
    """Send photographs to a vision provider and return validated observations.

    When a model is overloaded, walks down the provider's fallback chain rather
    than retrying the same busy model. Whichever model actually answered is
    reported on the result, so a mixed-model corpus stays visible instead of
    quietly averaging two models into one accuracy number.
    """
    try:
        vision_provider = get_provider(provider, client=client)
    except ProviderError as exc:
        raise VisionExtractionError(str(exc)) from exc

    system_prompt = load_system_prompt(prompts_path)
    images = prepare_images(image_paths)
    instruction = build_instruction(len(images))
    chain = resolve_model_chain(vision_provider, model, fallback, allow_lite, lite_only)

    last_error: ProviderError | None = None
    for index, candidate in enumerate(chain):
        try:
            return vision_provider.extract(
                system_prompt=system_prompt,
                images=images,
                instruction=instruction,
                model=candidate,
                effort=effort,
            )
        except ProviderError as exc:
            last_error = exc
            if is_rate_limited(str(exc)):
                # Remember this so later posts in a batch skip it outright.
                mark_rate_limited(candidate)
            is_last = index == len(chain) - 1
            # Only step down for transient trouble. A bad API key or a malformed
            # image fails identically on every model in the chain.
            if is_last or not is_transient_failure(str(exc)):
                raise VisionExtractionError(str(exc)) from exc
            print(
                f"  {candidate} unavailable ({_first_line(exc)}); falling back to {chain[index + 1]}",
                file=sys.stderr,
            )

    raise VisionExtractionError(str(last_error))  # pragma: no cover - chain is never empty


def _first_line(exc: Exception, limit: int = 80) -> str:
    text = str(exc).split("\n")[0]
    return text[:limit] + ("..." if len(text) > limit else "")


def analyse_locally(
    image_paths: list[Path | str], extraction: VisionExtraction
) -> LocalAnalysis:
    """Measurements Python makes for itself, from the pixels the LLM saw.

    The extractor says where the label is and what it says; this measures the
    letterforms against Apple's typeface. Nothing here calls an API, so it costs
    nothing and is deterministic - and it keeps the rules engine a pure function
    of data, since the result arrives there as numbers rather than an image.
    """
    analysis = LocalAnalysis()

    try:
        sharpness = sharpest(list(image_paths))
        if sharpness is not None:
            analysis = analysis.model_copy(update={"sharpness_score": sharpness.score})
    except Exception:
        pass  # Quality measurement must never break an extraction.

    region = LabelRegion.from_extraction(extraction)
    if region is None or not extraction.label_line_texts or not sf_available():
        return analysis

    index = region.image_index - 1
    if not (0 <= index < len(image_paths)):
        return analysis

    try:
        with Image.open(image_paths[index]) as source:
            grey = source.convert("L")
            crop = grey.crop(region.pixel_box(grey.width, grey.height))
        score = analyse_label_lines(crop, extraction.label_line_texts)
    except Exception:
        return analysis

    if score is None:
        return analysis

    return analysis.model_copy(update={
        "font_scatter_percent": score.scatter_percent if score.is_scoreable else None,
        "font_glyphs_measured": score.glyphs,
        "font_letters_measured": score.letters_measured,
        "median_glyph_height_px": score.median_glyph_height_px,
        "font_line_text": extraction.label_line_texts[0] if extraction.label_line_texts else None,
        "font_below_resolution_floor": score.median_glyph_height_px < MINIMUM_GLYPH_HEIGHT_PX,
        "font_shape_distance": score.shape_distance,
        "font_letterforms_resolved": score.is_measurable,
    })


__all__ = [
    "DEFAULT_EFFORT",
    "DEFAULT_PROVIDER",
    "MAX_IMAGE_EDGE_PX",
    "PROMPTS_PATH",
    "ProviderResult",
    "VisionExtractionError",
    "build_instruction",
    "ReferencePhotoError",
    "assert_not_reference_photo",
    "encode_image",
    "extract",
    "analyse_locally",
    "load_system_prompt",
    "prepare_images",
    "resolve_model_chain",
]
