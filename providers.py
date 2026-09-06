"""Vision providers: the swappable half of stage 1.

The rules engine is provider-agnostic by construction, and so is everything in
``vision_extractor`` except the API call itself. That call lives here, behind a
one-method protocol, for a reason that is about accuracy rather than tidiness:
the only way to know whether a free model is "cheap without sacrificing
accuracy" is to run both against the same photographs and compare ledgers.

Each provider is handed identical inputs - the same system prompt from
PROMPTS.md, the same downscaled images, the same ``VisionExtraction`` JSON
Schema - so any difference in the output is a difference in the model, not in
the harness.

Both SDKs are imported lazily, so neither is a hard dependency: installing only
``google-genai`` is enough to run the Gemini path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from schema import VisionExtraction

#: Canonical effort vocabulary, mapped onto each provider's own control.
EFFORT_LEVELS = ("low", "medium", "high")

#: Headroom for reasoning tokens plus the (tiny) JSON payload.
MAX_OUTPUT_TOKENS = 8192


class ProviderError(RuntimeError):
    """A provider could not turn images into a valid extraction."""


#: Failures worth retrying or downgrading past. Free-tier capacity is shared, so
#: overload 500s are routine. Anything not listed here (bad key, missing file,
#: schema violation) is permanent - retrying it just burns backoff cycles.
_TRANSIENT_MARKERS = (
    "rate limit",
    "server error",
    "high demand",
    "temporarily",
    "timeout",
    "timed out",
    "overloaded",
    "unavailable",
    "resource has been exhausted",
)


def is_transient_failure(message: str) -> bool:
    """True when retrying, or trying a different model, could plausibly help."""
    lowered = message.lower()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


@dataclass(frozen=True)
class PreparedImage:
    """One photograph, already downscaled and base64-encoded by the caller."""

    label: str
    media_type: str
    data_b64: str


@dataclass(frozen=True)
class ProviderResult:
    extraction: VisionExtraction
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    #: None means "not metered in dollars here" - the free tier, or a model
    #: missing from the pricing table. The CLI renders that distinctly rather
    #: than printing a misleading $0.00.
    estimated_cost_usd: float | None
    free_tier: bool


class VisionProvider(Protocol):
    name: str
    default_model: str

    def extract(
        self,
        *,
        system_prompt: str,
        images: list[PreparedImage],
        instruction: str,
        model: str,
        effort: str,
    ) -> ProviderResult: ...


def _status_code(exc: Exception) -> int | None:
    """Best-effort HTTP status from an SDK exception.

    Both SDKs expose ``status_code`` on their API errors, but the concrete
    classes live in private modules that churn between releases. Reading the
    attribute is stabler than importing the exception hierarchy.
    """
    return getattr(exc, "status_code", None)


def _explain(exc: Exception, provider: str, model: str) -> str:
    status = _status_code(exc)
    if status in (401, 403):
        return f"{provider} rejected the credentials for model '{model}': {exc}"
    if status == 404:
        return f"Model '{model}' is not available on {provider}: {exc}"
    if status == 429:
        return (
            f"{provider} rate limit reached. Free tiers cap requests per minute and per day; "
            f"wait and retry, or switch provider with --provider. ({exc})"
        )
    if status is not None and status >= 500:
        return f"{provider} had a server error ({status}); retry shortly: {exc}"
    return f"{provider} request failed: {exc}"


# --------------------------------------------------------------------------- #
# Anthropic (Claude)
# --------------------------------------------------------------------------- #

#: USD per million tokens (input, output). Rates drift - treat as an estimate.
ANTHROPIC_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

_CACHE_READ_DISCOUNT = 0.1
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicVisionProvider:
    """Claude via structured outputs - the response shape is enforced server-side."""

    name = "anthropic"
    default_model = "claude-opus-5"
    #: Tried in order when a model is overloaded. Each step is cheaper and a
    #: little less capable; all three read fine print acceptably.
    fallback_chain = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5")

    def __init__(self, client=None):
        self._client = client
        #: Dropped for the life of the process if the account lacks the beta.
        self._fallbacks_enabled = True

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ProviderError(
                    "The anthropic SDK is not installed. Run: pip install anthropic"
                ) from exc
            self._client = anthropic.Anthropic()
        return self._client

    def extract(
        self,
        *,
        system_prompt: str,
        images: list[PreparedImage],
        instruction: str,
        model: str,
        effort: str,
    ) -> ProviderResult:
        client = self._get_client()

        content: list[dict] = []
        for image in images:
            content.append({"type": "text", "text": image.label})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.media_type,
                        "data": image.data_b64,
                    },
                }
            )
        content.append({"type": "text", "text": instruction})

        request = {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            # A list block so the stable prompt carries a cache breakpoint. It
            # only engages above the model's minimum cacheable prefix, and the
            # savings are small next to image tokens either way.
            "system": [
                {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": "user", "content": content}],
            "output_format": VisionExtraction,
            "output_config": {"effort": effort},
        }

        response = self._call(client, request, model)

        if response.stop_reason == "refusal":
            raise ProviderError(
                "Claude declined to process these images "
                f"(category: {getattr(response.stop_details, 'category', None)})."
            )
        if response.stop_reason == "max_tokens":
            raise ProviderError(
                f"Extraction was truncated at max_tokens={MAX_OUTPUT_TOKENS}. Use fewer images."
            )

        extraction = response.parsed_output
        if extraction is None:
            raise ProviderError(
                "The response contained no parsable structured output. This usually means the "
                "images did not show an identifiable product."
            )

        usage = response.usage
        cached = getattr(usage, "cache_read_input_tokens", 0) or 0
        return ProviderResult(
            extraction=extraction,
            provider=self.name,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=cached,
            estimated_cost_usd=estimate_anthropic_cost(
                model, usage.input_tokens, usage.output_tokens, cached
            ),
            free_tier=False,
        )

    def _call(self, client, request: dict, model: str):
        """Call the API, degrading gracefully if server-side fallbacks are off.

        Server-side fallbacks reroute a refusal to another model instead of
        failing the request. Accounts without the beta get a 400, so we drop the
        parameter once rather than on every call.
        """
        import anthropic

        try:
            if self._fallbacks_enabled:
                return client.beta.messages.parse(
                    **request, fallbacks="default", betas=[_FALLBACK_BETA]
                )
            return client.messages.parse(**request)
        except anthropic.BadRequestError as exc:
            message = str(exc).lower()
            if self._fallbacks_enabled and ("fallback" in message or "beta" in message):
                self._fallbacks_enabled = False
                return client.messages.parse(**request)
            raise ProviderError(_explain(exc, "Claude", model)) from exc
        except Exception as exc:
            raise ProviderError(_explain(exc, "Claude", model)) from exc


def estimate_anthropic_cost(
    model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0
) -> float | None:
    rates = ANTHROPIC_PRICING.get(model)
    if rates is None:
        return None
    input_rate, output_rate = rates
    billable_input = max(0, input_tokens - cached_tokens)
    cost = (
        billable_input * input_rate
        + cached_tokens * input_rate * _CACHE_READ_DISCOUNT
        + output_tokens * output_rate
    ) / 1_000_000
    return round(cost, 6)


# --------------------------------------------------------------------------- #
# Google Gemini (AI Studio)
# --------------------------------------------------------------------------- #

#: Models Google lists as available on the AI Studio free tier. Flash-Lite is
#: cheaper still but weaker at reading fine print, which is the whole job here.
GEMINI_FREE_TIER_MODELS = frozenset(
    {
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
    }
)

#: Our canonical effort levels mapped onto Gemini's thinking_level.
_GEMINI_THINKING_LEVEL = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}


class GeminiVisionProvider:
    """Gemini via AI Studio, using response_format for schema-constrained JSON."""

    name = "gemini"
    default_model = "gemini-3.8-flash"
    #: Deliberately all full Flash models, no Flash-Lite. Lite is cheaper still
    #: but measurably weaker at small print, which is the entire job here - a
    #: silent downgrade to it would cost accuracy without anyone noticing.
    #: Reach it explicitly with --model gemini-3.5-flash-lite if you want it.
    #:
    #: Free-tier quota is metered PER MODEL, so each distinct model in this
    #: chain is extra daily capacity, not just extra redundancy. Verified live
    #: against the API with tools/check_models.py - gemini-2.5-flash was in the
    #: docs' free-tier list but returns 404 "no longer available to new users",
    #: so never add a model here without probing it first.
    fallback_chain = (
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    )

    def __init__(self, client=None):
        self._client = client

    def _get_client(self):
        if self._client is None:
            # Checked before the import so a missing key gives the actionable
            # message rather than an SDK ImportError.
            if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
                raise ProviderError(
                    "No Gemini credentials found. Create a free key at "
                    "https://aistudio.google.com/apikey and export it as GEMINI_API_KEY."
                )
            try:
                from google import genai
            except ImportError as exc:
                raise ProviderError(
                    "The Gemini SDK is not installed. Run: pip install google-genai"
                ) from exc
            self._client = genai.Client()
        return self._client

    def extract(
        self,
        *,
        system_prompt: str,
        images: list[PreparedImage],
        instruction: str,
        model: str,
        effort: str,
    ) -> ProviderResult:
        client = self._get_client()

        content: list[dict] = []
        for image in images:
            content.append({"type": "text", "text": image.label})
            content.append(
                {"type": "image", "data": image.data_b64, "mime_type": image.media_type}
            )
        content.append({"type": "text", "text": instruction})

        try:
            response = client.interactions.create(
                model=model,
                system_instruction=system_prompt,
                input=content,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    # The SDK serialises schema_ to the wire field "schema".
                    "schema_": VisionExtraction.model_json_schema(),
                },
                generation_config={
                    "thinking_level": _GEMINI_THINKING_LEVEL.get(effort, "medium"),
                    "max_output_tokens": MAX_OUTPUT_TOKENS,
                },
            )
        except Exception as exc:
            raise ProviderError(_explain(exc, "Gemini", model)) from exc

        payload = response.output_text
        if not payload:
            raise ProviderError(
                "Gemini returned no text output. This usually means the images were rejected "
                "by a safety filter or did not show an identifiable product."
            )

        try:
            extraction = VisionExtraction.model_validate_json(payload)
        except Exception as exc:
            # response_format constrains the output, but a truncated response
            # still yields invalid JSON - surface it rather than crashing.
            raise ProviderError(
                f"Gemini returned output that did not match the extraction schema: {exc}\n"
                f"Raw output: {payload[:400]}"
            ) from exc

        usage = response.usage
        return ProviderResult(
            extraction=extraction,
            provider=self.name,
            model=model,
            input_tokens=getattr(usage, "total_input_tokens", 0) or 0,
            output_tokens=getattr(usage, "total_output_tokens", 0) or 0,
            cached_tokens=getattr(usage, "total_cached_tokens", 0) or 0,
            estimated_cost_usd=0.0 if model in GEMINI_FREE_TIER_MODELS else None,
            free_tier=model in GEMINI_FREE_TIER_MODELS,
        )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

PROVIDERS: dict[str, type] = {
    "gemini": GeminiVisionProvider,
    "anthropic": AnthropicVisionProvider,
}

#: Gemini by default: the AI Studio free tier makes a verification cost nothing,
#: which is what keeps an un-paywalled tool viable.
DEFAULT_PROVIDER = os.environ.get("AIRPODS_VERIFIER_PROVIDER", "gemini")


def get_provider(name: str | None = None, *, client=None) -> VisionProvider:
    key = (name or DEFAULT_PROVIDER).lower()
    if key not in PROVIDERS:
        raise ProviderError(
            f"Unknown provider '{key}'. Available: {', '.join(sorted(PROVIDERS))}."
        )
    return PROVIDERS[key](client=client)
