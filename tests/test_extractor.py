"""Tests for image preparation, prompt loading, and both vision providers.

Nothing here touches the network. Each provider is driven by a stub client that
captures the request, so the request *shape* stays under test without spending
money or needing credentials in CI.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest

import providers as pv
import vision_extractor as ve
from schema import VisionExtraction

PIL_Image = pytest.importorskip("PIL.Image")

VALID_EXTRACTION = {
    "claimed_model_variant": "Pro 2 USB-C",
    "extracted_model_number": "A3047",
    "extracted_serial_number": "H4XQ7K2LMN9P",
    "copyright_year": 2023,
    "has_plastic_shrinkwrap": False,
    "has_paper_pull_tabs": True,
    "font_kerning_irregularities": False,
    "visible_spelling_errors": [],
}


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    """A deliberately oversized image, like a real phone photo."""
    path = tmp_path / "box.png"
    PIL_Image.new("RGB", (4032, 3024), "white").save(path)
    return path


# --------------------------------------------------------------------------- #
# Prompt + image preparation (shared by every provider)
# --------------------------------------------------------------------------- #


class TestPromptLoading:
    def test_loads_the_section_from_prompts_md(self):
        prompt = ve.load_system_prompt()
        assert "Optical Character Recognition" in prompt
        assert "claimed_model_variant" in prompt

    def test_missing_file_raises_a_clear_error(self, tmp_path):
        with pytest.raises(ve.VisionExtractionError, match="Could not read"):
            ve.load_system_prompt(tmp_path / "nope.md")

    def test_file_without_the_heading_raises(self, tmp_path):
        path = tmp_path / "PROMPTS.md"
        path.write_text("# Nothing useful here")
        with pytest.raises(ve.VisionExtractionError, match="does not contain"):
            ve.load_system_prompt(path)


class TestImageEncoding:
    def test_oversized_images_are_downscaled(self, photo):
        media_type, data = ve.encode_image(photo)
        assert media_type == "image/jpeg"
        with PIL_Image.open(io.BytesIO(base64.standard_b64decode(data))) as image:
            assert max(image.size) == ve.MAX_IMAGE_EDGE_PX

    def test_downscaling_actually_cuts_the_token_bill(self, photo):
        # Image tokens scale with pixel count, so this is the biggest cost lever
        # on a paid provider and the biggest rate-limit lever on a free one.
        _, data = ve.encode_image(photo)
        with PIL_Image.open(io.BytesIO(base64.standard_b64decode(data))) as image:
            assert image.width * image.height < (4032 * 3024) / 2

    def test_small_images_are_not_upscaled(self, tmp_path):
        path = tmp_path / "small.png"
        PIL_Image.new("RGB", (800, 600), "white").save(path)
        _, data = ve.encode_image(path)
        with PIL_Image.open(io.BytesIO(base64.standard_b64decode(data))) as image:
            assert image.size == (800, 600)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ve.VisionExtractionError, match="Image not found"):
            ve.encode_image(tmp_path / "absent.jpg")

    def test_non_image_file_raises(self, tmp_path):
        path = tmp_path / "notes.jpg"
        path.write_text("this is not an image")
        with pytest.raises(ve.VisionExtractionError, match="Could not open"):
            ve.encode_image(path)


class TestImagePreparation:
    def test_images_are_labelled_in_order(self, photo):
        prepared = ve.prepare_images([photo, photo])
        assert [image.label for image in prepared] == ["Image 1 (box.png):", "Image 2 (box.png):"]
        assert all(image.media_type == "image/jpeg" for image in prepared)

    def test_no_images_raises(self):
        with pytest.raises(ve.VisionExtractionError, match="At least one image"):
            ve.prepare_images([])

    def test_instruction_tells_the_model_to_prefer_null(self):
        assert "null" in ve.build_instruction(2)


# --------------------------------------------------------------------------- #
# Provider registry
# --------------------------------------------------------------------------- #


class TestProviderRegistry:
    def test_gemini_is_the_default(self):
        # The free tier is what keeps an un-paywalled tool viable.
        assert pv.DEFAULT_PROVIDER == "gemini"
        assert pv.get_provider().name == "gemini"

    def test_both_providers_are_selectable(self):
        assert pv.get_provider("anthropic").name == "anthropic"
        assert pv.get_provider("gemini").name == "gemini"

    def test_unknown_provider_lists_the_valid_ones(self):
        with pytest.raises(pv.ProviderError, match="Available: anthropic, gemini"):
            pv.get_provider("openai")

    def test_default_gemini_model_is_on_the_free_tier(self):
        provider = pv.get_provider("gemini")
        assert provider.default_model in pv.GEMINI_FREE_TIER_MODELS


# --------------------------------------------------------------------------- #
# Anthropic provider
# --------------------------------------------------------------------------- #


class _AnthropicResponse:
    stop_reason = "end_turn"
    stop_details = None

    def __init__(self, extraction):
        self._extraction = extraction
        self.usage = type("Usage", (), {
            "input_tokens": 2000, "output_tokens": 120, "cache_read_input_tokens": 0,
        })()

    @property
    def parsed_output(self):
        return self._extraction


class _AnthropicStubClient:
    def __init__(self, response):
        self.captured: dict = {}
        outer = self

        class _Messages:
            def parse(self, **kwargs):
                outer.captured = kwargs
                return response

        self.messages = _Messages()
        self.beta = type("Beta", (), {"messages": _Messages()})()


class TestAnthropicProvider:
    def _extract(self, photo, response):
        client = _AnthropicStubClient(response)
        provider = pv.AnthropicVisionProvider(client=client)
        provider._fallbacks_enabled = False  # exercise the non-beta path
        result = provider.extract(
            system_prompt="SYSTEM",
            images=ve.prepare_images([photo]),
            instruction="INSTRUCTION",
            model="claude-opus-5",
            effort="low",
        )
        return result, client.captured

    def test_request_carries_the_schema_and_a_cache_breakpoint(self, photo):
        extraction = VisionExtraction.model_validate(VALID_EXTRACTION)
        result, request = self._extract(photo, _AnthropicResponse(extraction))

        assert result.extraction is extraction
        assert result.provider == "anthropic"
        assert result.free_tier is False
        assert result.estimated_cost_usd > 0
        # The API, not the prompt, enforces the extraction boundary.
        assert request["output_format"] is VisionExtraction
        assert request["output_config"] == {"effort": "low"}
        assert request["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_refusal_is_reported_not_swallowed(self, photo):
        response = _AnthropicResponse(None)
        response.stop_reason = "refusal"
        with pytest.raises(pv.ProviderError, match="declined"):
            self._extract(photo, response)

    def test_truncation_is_reported(self, photo):
        response = _AnthropicResponse(None)
        response.stop_reason = "max_tokens"
        with pytest.raises(pv.ProviderError, match="truncated"):
            self._extract(photo, response)

    def test_unparsable_response_is_reported(self, photo):
        with pytest.raises(pv.ProviderError, match="no parsable structured output"):
            self._extract(photo, _AnthropicResponse(None))

    def test_pricing(self):
        # 1M input + 1M output on Opus 5 = $5 + $25.
        assert pv.estimate_anthropic_cost("claude-opus-5", 1_000_000, 1_000_000) == pytest.approx(30.0)
        assert pv.estimate_anthropic_cost("claude-opus-5", 10_000, 0, 10_000) < pv.estimate_anthropic_cost(
            "claude-opus-5", 10_000, 0, 0
        )
        assert pv.estimate_anthropic_cost("some-future-model", 1000, 100) is None


# --------------------------------------------------------------------------- #
# Gemini provider
# --------------------------------------------------------------------------- #


class _GeminiResponse:
    def __init__(self, output_text):
        self.output_text = output_text
        self.usage = type("Usage", (), {
            "total_input_tokens": 1800, "total_output_tokens": 110, "total_cached_tokens": 0,
        })()


class _GeminiStubClient:
    def __init__(self, response):
        self.captured: dict = {}
        outer = self

        class _Interactions:
            def create(self, **kwargs):
                outer.captured = kwargs
                return response

        self.interactions = _Interactions()


class TestGeminiProvider:
    def _extract(self, photo, response, **overrides):
        client = _GeminiStubClient(response)
        provider = pv.GeminiVisionProvider(client=client)
        kwargs = {
            "system_prompt": "SYSTEM",
            "images": ve.prepare_images([photo]),
            "instruction": "INSTRUCTION",
            "model": "gemini-3.8-flash",
            "effort": "medium",
        }
        kwargs.update(overrides)
        return provider.extract(**kwargs), client.captured

    def test_request_constrains_output_to_the_extraction_schema(self, photo):
        import json

        result, request = self._extract(photo, _GeminiResponse(json.dumps(VALID_EXTRACTION)))

        assert result.extraction.extracted_model_number == "A3047"
        assert result.provider == "gemini"
        assert request["model"] == "gemini-3.8-flash"
        assert request["system_instruction"] == "SYSTEM"
        # schema_ is the SDK field name; it serialises to the wire field "schema".
        response_format = request["response_format"]
        assert response_format["mime_type"] == "application/json"
        assert response_format["schema_"] == VisionExtraction.model_json_schema()

    def test_images_are_sent_inline_with_a_mime_type(self, photo):
        import json

        _, request = self._extract(photo, _GeminiResponse(json.dumps(VALID_EXTRACTION)))
        image_blocks = [block for block in request["input"] if block["type"] == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["mime_type"] == "image/jpeg"
        assert image_blocks[0]["data"]

    def test_free_tier_model_reports_zero_cost(self, photo):
        import json

        result, _ = self._extract(photo, _GeminiResponse(json.dumps(VALID_EXTRACTION)))
        assert result.free_tier is True
        assert result.estimated_cost_usd == 0.0

    def test_paid_model_is_not_labelled_free(self, photo):
        import json

        result, _ = self._extract(
            photo, _GeminiResponse(json.dumps(VALID_EXTRACTION)), model="gemini-3-pro-paid"
        )
        assert result.free_tier is False
        assert result.estimated_cost_usd is None

    @pytest.mark.parametrize(
        "effort, expected",
        [("low", "low"), ("medium", "medium"), ("high", "high"), ("max", "high")],
    )
    def test_effort_maps_onto_gemini_thinking_level(self, photo, effort, expected):
        import json

        _, request = self._extract(
            photo, _GeminiResponse(json.dumps(VALID_EXTRACTION)), effort=effort
        )
        assert request["generation_config"]["thinking_level"] == expected

    def test_empty_output_is_reported(self, photo):
        with pytest.raises(pv.ProviderError, match="no text output"):
            self._extract(photo, _GeminiResponse(""))

    def test_schema_violating_output_is_reported_with_the_raw_text(self, photo):
        with pytest.raises(pv.ProviderError, match="did not match the extraction schema"):
            self._extract(photo, _GeminiResponse('{"claimed_model_variant": "Pro 3"}'))

    def test_missing_credentials_gives_an_actionable_message(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(pv.ProviderError, match="aistudio.google.com"):
            pv.GeminiVisionProvider()._get_client()


# --------------------------------------------------------------------------- #
# Provider parity
# --------------------------------------------------------------------------- #


class TestProviderParity:
    def test_both_providers_receive_identical_prompt_and_images(self, photo):
        """The harness must not be a confound when comparing model accuracy."""
        import json

        system_prompt = ve.load_system_prompt()
        images = ve.prepare_images([photo])
        instruction = ve.build_instruction(1)

        anthropic_client = _AnthropicStubClient(
            _AnthropicResponse(VisionExtraction.model_validate(VALID_EXTRACTION))
        )
        anthropic_provider = pv.AnthropicVisionProvider(client=anthropic_client)
        anthropic_provider._fallbacks_enabled = False
        anthropic_provider.extract(
            system_prompt=system_prompt, images=images, instruction=instruction,
            model="claude-opus-5", effort="medium",
        )

        gemini_client = _GeminiStubClient(_GeminiResponse(json.dumps(VALID_EXTRACTION)))
        pv.GeminiVisionProvider(client=gemini_client).extract(
            system_prompt=system_prompt, images=images, instruction=instruction,
            model="gemini-3.8-flash", effort="medium",
        )

        assert anthropic_client.captured["system"][0]["text"] == system_prompt
        assert gemini_client.captured["system_instruction"] == system_prompt

        anthropic_image = anthropic_client.captured["messages"][0]["content"][1]["source"]["data"]
        gemini_image = [
            block for block in gemini_client.captured["input"] if block["type"] == "image"
        ][0]["data"]
        assert anthropic_image == gemini_image
