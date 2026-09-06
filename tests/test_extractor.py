"""Tests for the vision extractor's pure helpers.

Nothing here touches the network. The API call itself is covered by a stubbed
client so the request shape stays under test without spending money.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest

import vision_extractor as ve
from schema import VisionExtraction

PIL_Image = pytest.importorskip("PIL.Image")


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    """A deliberately oversized image, like a real phone photo."""
    path = tmp_path / "box.png"
    PIL_Image.new("RGB", (4032, 3024), "white").save(path)
    return path


class TestPromptLoading:
    def test_loads_the_section_from_prompts_md(self):
        prompt = ve.load_system_prompt()
        assert "Optical Character Recognition" in prompt
        # The extraction contract itself must reach the model.
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
        # Image tokens are ~(w*h)/750, so this is the single biggest cost lever.
        _, data = ve.encode_image(photo)
        with PIL_Image.open(io.BytesIO(base64.standard_b64decode(data))) as image:
            resized_tokens = (image.width * image.height) / 750
        assert resized_tokens < (4032 * 3024) / 750 / 2

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


class TestUserContent:
    def test_images_are_labelled_and_followed_by_an_instruction(self, photo):
        content = ve.build_user_content([photo, photo])
        assert [block["type"] for block in content] == [
            "text", "image", "text", "image", "text",
        ]
        assert "Image 1" in content[0]["text"]
        assert "Image 2" in content[2]["text"]
        assert "null" in content[-1]["text"]

    def test_no_images_raises(self):
        with pytest.raises(ve.VisionExtractionError, match="At least one image"):
            ve.build_user_content([])


class TestCostEstimation:
    def test_known_model_is_priced(self):
        # 1M input + 1M output on Opus 5 = $5 + $25.
        assert ve.estimate_cost("claude-opus-5", 1_000_000, 1_000_000) == pytest.approx(30.0)

    def test_cached_tokens_are_discounted(self):
        full = ve.estimate_cost("claude-opus-5", 10_000, 0, 0)
        cached = ve.estimate_cost("claude-opus-5", 10_000, 0, 10_000)
        assert cached < full

    def test_unknown_model_returns_none(self):
        assert ve.estimate_cost("some-future-model", 1000, 100) is None


class _StubResponse:
    stop_reason = "end_turn"
    stop_details = None

    def __init__(self, extraction: VisionExtraction):
        self._extraction = extraction
        self.usage = type("Usage", (), {
            "input_tokens": 2000, "output_tokens": 120, "cache_read_input_tokens": 0,
        })()

    @property
    def parsed_output(self):
        return self._extraction


class _StubClient:
    """Captures the request instead of sending it."""

    def __init__(self, response):
        self.captured: dict = {}
        outer = self

        class _Parse:
            def parse(self, **kwargs):
                outer.captured = kwargs
                return response

        self.messages = _Parse()
        self.beta = type("Beta", (), {"messages": _Parse()})()


class TestExtractRequestShape:
    @pytest.fixture(autouse=True)
    def _disable_fallbacks(self, monkeypatch):
        # Exercise the non-beta path; the fallback path is a thin wrapper.
        monkeypatch.setattr(ve, "_fallbacks_enabled", False)

    def test_request_carries_the_schema_and_a_cache_breakpoint(self, photo):
        extraction = VisionExtraction.model_validate({
            "claimed_model_variant": "Pro 2 USB-C",
            "extracted_model_number": "A3047",
            "extracted_serial_number": "H4XQ7K2LMN9P",
            "copyright_year": 2023,
            "has_plastic_shrinkwrap": False,
            "has_paper_pull_tabs": True,
            "font_kerning_irregularities": False,
            "visible_spelling_errors": [],
        })
        client = _StubClient(_StubResponse(extraction))

        result = ve.extract([photo], client=client, effort="low")

        assert result.extraction is extraction
        assert result.estimated_cost_usd > 0
        request = client.captured
        # The API, not the prompt, enforces the extraction boundary.
        assert request["output_format"] is VisionExtraction
        assert request["output_config"] == {"effort": "low"}
        assert request["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_refusal_is_reported_not_swallowed(self, photo):
        response = _StubResponse(None)
        response.stop_reason = "refusal"
        with pytest.raises(ve.VisionExtractionError, match="declined"):
            ve.extract([photo], client=_StubClient(response))

    def test_truncation_is_reported(self, photo):
        response = _StubResponse(None)
        response.stop_reason = "max_tokens"
        with pytest.raises(ve.VisionExtractionError, match="truncated"):
            ve.extract([photo], client=_StubClient(response))

    def test_unparsable_response_is_reported(self, photo):
        with pytest.raises(ve.VisionExtractionError, match="no parsable structured output"):
            ve.extract([photo], client=_StubClient(_StubResponse(None)))
