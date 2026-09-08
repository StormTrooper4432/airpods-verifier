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
PIL_ImageStat = pytest.importorskip("PIL.ImageStat")

VALID_EXTRACTION = {
    "claimed_model_variant": "Pro 2 USB-C",
    "extracted_model_number": "A3047",
    "extracted_serial_number": "H4XQ7K2LMN9P",
    "serial_label_literal": "(S) Serial No. H4XQ7K2LMN9P",
    "part_number": "MTJV3AM/A",
    "origin_line": "Assembled in Vietnam",
    "serial_case_lid": None,
    "serial_left_earbud": None,
    "serial_right_earbud": None,
    "label_image_index": None,
    "label_left": None,
    "label_top": None,
    "label_right": None,
    "label_bottom": None,
    "label_line_texts": [],
    "copyright_year": 2023,
    "has_plastic_shrinkwrap": False,
    "has_paper_pull_tabs": True,
    "shrinkwrap_has_pull_tab": None,
    "shrinkwrap_looks_shoddy": None,
    "visible_panels": ["box_bottom_label"],
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


class TestOrientationIsCorrectedBeforeTheModelSeesIt:
    """A phone stores sensor pixels plus a tag saying which way up they are.

    Browsers apply that tag, so the person uploading sees their photograph
    upright and has no reason to report a problem; Pillow ignores it and the
    JPEG re-encode drops it, so the model was being handed a sideways or
    upside-down box and asked to read the fine print on it. That is invisible
    from both ends, and it is why this is tested rather than eyeballed.

    Found on a real upload: an AirPods box photographed with the phone turned
    over carried orientation 3, and every printed field on it came back wrong.
    """

    @staticmethod
    def _tagged(path, orientation, size=(400, 200)):
        """A frame whose top-left corner is black, saved with an EXIF tag.

        Where that corner ends up says exactly which transform was applied.
        """
        image = PIL_Image.new("RGB", size, "white")
        for x in range(size[0] // 4):
            for y in range(size[1] // 4):
                image.putpixel((x, y), (0, 0, 0))
        exif = image.getexif()
        exif[274] = orientation
        image.save(path, format="JPEG", quality=95, exif=exif)
        return path

    @staticmethod
    def _darkest_corner(image):
        w, h = image.size
        boxes = {
            "top-left": (0, 0, w // 4, h // 4),
            "top-right": (w - w // 4, 0, w, h // 4),
            "bottom-left": (0, h - h // 4, w // 4, h),
            "bottom-right": (w - w // 4, h - h // 4, w, h),
        }
        return min(boxes, key=lambda name: PIL_ImageStat.Stat(image.crop(boxes[name])).mean[0])

    def _sent(self, path):
        _, data = ve.encode_image(path)
        return PIL_Image.open(io.BytesIO(base64.standard_b64decode(data))).convert("L")

    def test_an_upside_down_photograph_arrives_upright(self, tmp_path):
        """Orientation 3. The case that was actually found in the wild."""
        path = self._tagged(tmp_path / "flipped.jpg", 3)
        assert self._darkest_corner(self._sent(path)) == "bottom-right"

    def test_a_portrait_photograph_arrives_upright(self, tmp_path):
        """Orientation 6 - what a phone held normally writes, and the most
        common tag there is. The corpus is screenshots, which carry no tag at
        all, so no amount of corpus testing would have caught this."""
        path = self._tagged(tmp_path / "portrait.jpg", 6)
        assert self._darkest_corner(self._sent(path)) == "top-right"

    def test_a_rotated_photograph_has_its_sides_swapped(self, tmp_path):
        """A 90-degree tag means the stored width is the displayed height.
        Getting this wrong crops every region in the wrong place."""
        path = self._tagged(tmp_path / "sideways.jpg", 6, size=(400, 200))
        sent = self._sent(path)
        assert sent.size == (200, 400)

    def test_an_untagged_photograph_is_left_alone(self, tmp_path):
        """The whole existing corpus is untagged. This must be a no-op there,
        or every recorded corpus result silently changes meaning."""
        plain = PIL_Image.new("RGB", (400, 200), "white")
        for x in range(100):
            for y in range(50):
                plain.putpixel((x, y), (0, 0, 0))
        path = tmp_path / "plain.jpg"
        plain.save(path, format="JPEG", quality=95)
        sent = self._sent(path)
        assert sent.size == (400, 200)
        assert self._darkest_corner(sent) == "top-left"

    def test_orientation_one_is_also_left_alone(self, tmp_path):
        """Tagged, but tagged as already upright."""
        path = self._tagged(tmp_path / "upright.jpg", 1)
        assert self._darkest_corner(self._sent(path)) == "top-left"


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


# --------------------------------------------------------------------------- #
# Model fallback
# --------------------------------------------------------------------------- #


class _ScriptedProvider:
    """Fails with scripted errors, then succeeds. Records which models it saw."""

    name = "scripted"
    default_model = "model-a"
    fallback_chain = ("model-a", "model-b", "model-c")

    def __init__(self, failures: dict):
        self.failures = failures
        self.attempted: list[str] = []

    def extract(self, *, system_prompt, images, instruction, model, effort):
        self.attempted.append(model)
        if model in self.failures:
            raise pv.ProviderError(self.failures[model])
        return pv.ProviderResult(
            extraction=VisionExtraction.model_validate(VALID_EXTRACTION),
            provider=self.name, model=model, input_tokens=100, output_tokens=10,
            cached_tokens=0, estimated_cost_usd=0.0, free_tier=True,
        )


@pytest.fixture
def scripted(monkeypatch):
    def install(failures):
        provider = _ScriptedProvider(failures)
        monkeypatch.setattr(ve, "get_provider", lambda name, client=None: provider)
        return provider
    return install


class TestModelFallback:
    def test_overload_steps_down_the_chain(self, photo, scripted):
        provider = scripted({"model-a": "model-a is currently experiencing high demand"})
        result = ve.extract([photo])
        assert provider.attempted == ["model-a", "model-b"]
        # The result reports which model actually answered, so a mixed-model
        # corpus stays visible instead of being averaged into one number.
        assert result.model == "model-b"

    def test_it_walks_past_multiple_outages(self, photo, scripted):
        provider = scripted({
            "model-a": "server error (500)",
            "model-b": "Service temporarily unavailable",
        })
        assert ve.extract([photo]).model == "model-c"
        assert provider.attempted == ["model-a", "model-b", "model-c"]

    def test_permanent_failures_do_not_downgrade(self, photo, scripted):
        # A bad key fails identically on every model; stepping down the chain
        # would just produce three identical errors and hide the real cause.
        provider = scripted({"model-a": "No Gemini credentials found. Create a free key"})
        with pytest.raises(ve.VisionExtractionError, match="credentials"):
            ve.extract([photo])
        assert provider.attempted == ["model-a"]

    def test_exhausting_the_chain_raises_the_last_error(self, photo, scripted):
        provider = scripted({m: "high demand" for m in _ScriptedProvider.fallback_chain})
        with pytest.raises(ve.VisionExtractionError, match="high demand"):
            ve.extract([photo])
        assert provider.attempted == list(_ScriptedProvider.fallback_chain)

    def test_an_explicit_model_is_never_silently_swapped(self, photo, scripted):
        # Pinning a model is how you benchmark two models on the same photos.
        # Falling back would corrupt exactly that comparison.
        provider = scripted({"model-a": "high demand"})
        with pytest.raises(ve.VisionExtractionError):
            ve.extract([photo], model="model-a")
        assert provider.attempted == ["model-a"]

    def test_fallback_can_be_disabled(self, photo, scripted):
        provider = scripted({"model-a": "high demand"})
        with pytest.raises(ve.VisionExtractionError):
            ve.extract([photo], fallback=False)
        assert provider.attempted == ["model-a"]


class TestChainResolution:
    def test_real_providers_declare_chains_headed_by_their_default(self):
        for name in ("gemini", "anthropic"):
            provider = pv.get_provider(name)
            assert provider.fallback_chain[0] == provider.default_model
            assert len(provider.fallback_chain) >= 2

    def test_default_chain_is_lite(self):
        # ~500 requests/day per Lite model against ~20 for Flash. Labelled data
        # is the binding constraint, so throughput wins over fine-print quality.
        provider = pv.get_provider("gemini")
        assert all("lite" in m for m in provider.fallback_chain)
        assert provider.default_model in provider.fallback_chain

    def test_full_flash_is_reachable_when_quality_matters(self):
        provider = pv.get_provider("gemini")
        assert provider.flash_chain
        assert set(provider.flash_chain) <= set(provider.extended_fallback_chain)

    def test_gemini_chain_is_entirely_free_tier(self):
        provider = pv.get_provider("gemini")
        assert set(provider.fallback_chain) <= pv.GEMINI_FREE_TIER_MODELS
        assert set(provider.extended_fallback_chain) <= pv.GEMINI_FREE_TIER_MODELS

    def test_no_chain_contains_a_retired_model(self):
        provider = pv.get_provider("gemini")
        assert not pv.GEMINI_RETIRED_MODELS & set(provider.extended_fallback_chain)
        assert not pv.GEMINI_RETIRED_MODELS & pv.GEMINI_FREE_TIER_MODELS


class TestRateLimitCooldown:
    """Without cooldown memory, every post in a batch re-attempts each
    exhausted model and eats a full round-trip per model per post."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        pv.reset_cooldowns()
        yield
        pv.reset_cooldowns()

    def test_rate_limited_models_move_to_the_back_of_the_chain(self, photo, scripted):
        provider = scripted({"model-a": "Gemini rate limit reached"})
        ve.extract([photo])  # trips the cooldown on model-a
        assert pv.is_cooling_down("model-a")

        provider.attempted.clear()
        provider.failures = {}
        ve.extract([photo])
        # model-a is skipped, not retried first.
        assert provider.attempted[0] == "model-b"

    def test_cooldown_expires(self):
        pv.mark_rate_limited("model-x", now=0.0)
        assert pv.is_cooling_down("model-x", now=1.0)
        assert not pv.is_cooling_down("model-x", now=pv.MODEL_COOLDOWN_SECONDS + 1.0)

    def test_all_cooling_down_still_tries_something(self, photo, scripted):
        scripted({})
        for m in _ScriptedProvider.fallback_chain:
            pv.mark_rate_limited(m)
        # Never return an empty chain - a doomed attempt beats no attempt.
        assert ve.extract([photo]).model in _ScriptedProvider.fallback_chain

    def test_non_rate_limit_failures_do_not_trip_cooldown(self, photo, scripted):
        scripted({"model-a": "server error (500)"})
        ve.extract([photo])
        assert not pv.is_cooling_down("model-a")


class TestExtendedChain:
    def test_extending_reaches_full_flash(self):
        provider = pv.get_provider("gemini")
        assert not set(provider.flash_chain) & set(provider.fallback_chain)
        assert set(provider.flash_chain) <= set(provider.extended_fallback_chain)

    def test_extended_chain_keeps_quality_models_first(self):
        provider = pv.get_provider("gemini")
        extended = provider.extended_fallback_chain
        assert extended[: len(provider.fallback_chain)] == provider.fallback_chain

    def test_retired_models_are_absent(self):
        # All probed live and returning 404 "no longer available to new users".
        assert not pv.GEMINI_RETIRED_MODELS & set(
            pv.get_provider("gemini").extended_fallback_chain
        )


class TestLiteOnlyChain:
    """When the full-Flash family is out of daily quota, walking it first costs
    five failed round-trips per post before reaching a model that can answer."""

    def test_lite_only_skips_the_flash_family(self):
        provider = pv.get_provider("gemini")
        chain = ve.resolve_model_chain(provider, None, fallback=True, lite_only=True)
        assert chain == list(provider.lite_only_chain)
        assert not set(chain) & set(provider.flash_chain)

    def test_lite_only_still_has_internal_fallback(self):
        chain = ve.resolve_model_chain(
            pv.get_provider("gemini"), None, fallback=True, lite_only=True
        )
        assert len(chain) >= 2

    def test_lite_only_beats_allow_lite_when_both_are_set(self):
        provider = pv.get_provider("gemini")
        chain = ve.resolve_model_chain(
            provider, None, fallback=True, allow_lite=True, lite_only=True
        )
        assert chain == list(provider.lite_only_chain)

    def test_an_explicit_model_still_pins(self):
        chain = ve.resolve_model_chain(
            pv.get_provider("gemini"), "gemini-3.8-flash", fallback=True, lite_only=True
        )
        assert chain == ["gemini-3.8-flash"]

    def test_lite_only_chain_is_free_tier_and_not_retired(self):
        provider = pv.get_provider("gemini")
        assert set(provider.lite_only_chain) <= pv.GEMINI_FREE_TIER_MODELS
        assert not pv.GEMINI_RETIRED_MODELS & set(provider.lite_only_chain)


class TestReferencePhotoProtection:
    """Genuine reference photographs carry real serial numbers. The Gemini free
    tier states submitted content is used to improve Google's products, so a
    valid Apple serial would be retained by a third party - and a cloned valid
    serial passes Apple's own coverage lookup, which is the fraud this project
    exists to detect. Everything the baseline needs is measured locally."""

    def test_reference_photos_are_refused(self, tmp_path):
        protected = tmp_path / "knowledge" / "real_app2"
        protected.mkdir(parents=True)
        photo = protected / "IMG_0001.JPG"
        PIL_Image.new("RGB", (100, 100), "white").save(photo)
        with pytest.raises(ve.ReferencePhotoError, match="Refusing to send"):
            ve.prepare_images([photo])

    def test_the_refusal_explains_why(self, tmp_path):
        protected = tmp_path / "knowledge" / "real_ap4"
        protected.mkdir(parents=True)
        photo = protected / "x.jpg"
        PIL_Image.new("RGB", (50, 50), "white").save(photo)
        with pytest.raises(ve.ReferencePhotoError, match="measured locally"):
            ve.assert_not_reference_photo(photo)

    def test_ordinary_corpus_photos_are_unaffected(self, photo):
        assert ve.prepare_images([photo])

    @pytest.mark.parametrize("folder", ["real_app2", "real_ap1", "real_ap4w_anc", "real_app1"])
    def test_every_reference_directory_is_covered(self, tmp_path, folder):
        target = tmp_path / "knowledge" / folder
        target.mkdir(parents=True)
        photo = target / "img.jpg"
        PIL_Image.new("RGB", (40, 40), "white").save(photo)
        with pytest.raises(ve.ReferencePhotoError):
            ve.assert_not_reference_photo(photo)

    def test_the_block_survives_a_relative_path(self, tmp_path, monkeypatch):
        target = tmp_path / "knowledge" / "real_app2"
        target.mkdir(parents=True)
        photo = target / "img.jpg"
        PIL_Image.new("RGB", (40, 40), "white").save(photo)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ve.ReferencePhotoError):
            ve.assert_not_reference_photo("knowledge/real_app2/img.jpg")


class TestLocalAnalysis:
    """Pixel measurements Python makes for itself. No API call, so they cost
    nothing and are deterministic - and the rules engine still receives only
    numbers, never an image."""

    def _extraction(self, **overrides):
        return VisionExtraction.model_validate({**VALID_EXTRACTION, **overrides})

    def test_sharpness_is_always_measured(self, photo):
        analysis = ve.analyse_locally([photo], self._extraction())
        assert analysis.sharpness_score is not None

    def test_no_label_region_means_no_font_measurement(self, photo):
        analysis = ve.analyse_locally([photo], self._extraction())
        assert analysis.font_scatter_percent is None

    def test_a_label_region_out_of_range_is_ignored(self, photo):
        analysis = ve.analyse_locally([photo], self._extraction(
            label_image_index=9, label_left=0.1, label_top=0.1,
            label_right=0.9, label_bottom=0.3, label_line_texts=["CERTS KGZ"],
        ))
        assert analysis.font_scatter_percent is None

    def test_an_unreadable_image_does_not_break_extraction(self, tmp_path):
        broken = tmp_path / "broken.jpg"
        broken.write_text("not an image")
        analysis = ve.analyse_locally([broken], self._extraction(
            label_image_index=1, label_left=0.0, label_top=0.0,
            label_right=1.0, label_bottom=1.0, label_line_texts=["CERTS"],
        ))
        assert analysis.font_scatter_percent is None

    def test_a_rendered_label_measures_as_san_francisco(self, tmp_path):
        from PIL import ImageDraw, ImageFont

        import font_forensics as ff

        if not ff.sf_available():
            pytest.skip("San Francisco not present")
        lines = ["Designed by Apple in California", "Assembled in Vietnam",
                 "MTJV3AM/A AirPods Pro 2nd generation"]
        font = ImageFont.truetype(ff.SF_FONT_PATH, 90)
        image = PIL_Image.new("L", (2200, 500), 250)
        draw = ImageDraw.Draw(image)
        for index, text in enumerate(lines):
            draw.text((40, 40 + index * 150), text, font=font, fill=20)
        path = tmp_path / "label.png"
        image.convert("RGB").save(path)

        analysis = ve.analyse_locally([path], self._extraction(
            label_image_index=1, label_left=0.0, label_top=0.0,
            label_right=1.0, label_bottom=1.0, label_line_texts=lines,
        ))
        assert analysis.font_glyphs_measured and analysis.font_glyphs_measured > 10
        assert analysis.font_scatter_percent is not None
        assert analysis.font_scatter_percent < ff.FONT_SCATTER_THRESHOLD

    def test_the_rules_engine_still_receives_only_numbers(self):
        # The architectural boundary: no rule may touch an image.
        import inspect

        import rules_engine

        source = inspect.getsource(rules_engine)
        # Check imports, not prose - the ledger legitimately says "Images show:".
        imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        assert not any("PIL" in line or "Image" in line for line in imports)


class TestPhoneFormats:
    """HEIC is what an iPhone shoots, and this tool asks for phone photographs.

    Pillow does not decode it. The upload control advertised image/heic,
    requirements.txt claimed Pillow gave "HEIC/phone-format normalisation", and
    encode_image's own docstring said it handled HEIC — none of it true, and a
    real iPhone photograph came back as "cannot identify image file". Three
    separate places asserted a capability nobody had tested.
    """

    def test_the_decoder_is_registered_rather_than_assumed(self):
        import image_formats

        assert image_formats.register() is image_formats.HEIF_SUPPORTED

    def test_registering_twice_is_harmless(self):
        import image_formats

        assert image_formats.register() == image_formats.register()

    def test_a_heic_photograph_encodes_for_the_model(self, tmp_path):
        import image_formats

        if not image_formats.HEIF_SUPPORTED:
            pytest.skip("pillow-heif is not installed in this environment")

        import pillow_heif

        source = PIL_Image.new("RGB", (800, 600), "white")
        for x in range(200):
            for y in range(150):
                source.putpixel((x, y), (10, 10, 10))
        path = tmp_path / "from_a_phone.heic"
        pillow_heif.from_pillow(source).save(str(path), quality=90)

        media_type, data = ve.encode_image(path)
        assert media_type == "image/jpeg"
        decoded = PIL_Image.open(io.BytesIO(base64.standard_b64decode(data)))
        assert decoded.size == (800, 600)

    def test_every_format_the_upload_advertises_can_actually_be_decoded(self):
        """The browser must not accept a file the pipeline then refuses.

        This is the check that was missing: the accept list and the decoder
        support were maintained in two places and drifted.
        """
        import re
        from pathlib import Path

        import image_formats

        boxnet = Path(__file__).resolve().parent.parent / "web/src/components/BoxNet.astro"
        if not boxnet.exists():
            pytest.skip("web frontend not present")

        accept = re.search(r'accept="([^"]+)"', boxnet.read_text()).group(1)  # type: ignore
        advertised = {token.strip().lower() for token in accept.split(",")}

        PIL_Image.init()
        for token in advertised:
            suffix = token if token.startswith(".") else "." + token.split("/")[-1]
            if suffix in (".heic", ".heif"):
                assert image_formats.HEIF_SUPPORTED, (
                    f"the upload advertises {token} but nothing can decode it"
                )
                continue
            assert suffix in PIL_Image.EXTENSION, f"upload advertises {token}, Pillow cannot open it"


class TestTheChainCommentMatchesTheChain:
    """A comment that contradicts the code under it is worse than no comment.

    `fallback_chain` carried "deliberately all full Flash models, no Flash-Lite
    ... a silent downgrade to it would cost accuracy without anyone noticing"
    directly above three Flash-Lite models. The chain had been changed and the
    prose had not, so the file asserted the opposite of what it did — and warned
    about exactly the thing that had already happened. Nothing caught it because
    prose is not executed.
    """

    @staticmethod
    def _comment_above(name: str) -> str:
        """The comment on the GEMINI chain specifically.

        Scoped to the class, not the module: `fallback_chain` exists on the
        Anthropic provider too and appears first in the file, so searching the
        whole source read the wrong comment and passed no matter what the
        Gemini one said. That mistake made the first version of these tests
        vacuous — they went green with the false comment restored.
        """
        import inspect

        source = inspect.getsource(pv.GeminiVisionProvider)
        at = source.index(f"    {name} = (")
        block = source[:at].rsplit("\n\n", 1)[-1]
        return " ".join(line.strip(" #:") for line in block.splitlines()).lower()

    def test_a_chain_of_lite_models_is_not_described_as_having_none(self):
        chain = pv.GeminiVisionProvider.fallback_chain
        if not all("lite" in model for model in chain):
            pytest.skip("the chain is no longer all Flash-Lite")
        comment = self._comment_above("fallback_chain")
        assert "no flash-lite" not in comment, (
            "the chain is entirely Flash-Lite and the comment says it contains none"
        )

    def test_the_chain_is_not_claimed_to_be_full_flash(self):
        chain = pv.GeminiVisionProvider.fallback_chain
        if not all("lite" in model for model in chain):
            pytest.skip("the chain is no longer all Flash-Lite")
        comment = self._comment_above("fallback_chain")
        assert "all full flash models" not in comment, (
            "the chain is entirely Flash-Lite and the comment calls it all full Flash"
        )

    def test_the_weaker_at_small_print_claim_is_not_reasserted_unsourced(self):
        """It was never measured, and measurement did not support it.

        7 posts on gemini-3.8-flash read identical identifying fields to Lite.
        If anyone reasserts it, the comment must carry the measurement saying so.
        """
        comment = self._comment_above("fallback_chain")
        if "measurably weaker at small print" in comment:
            assert "measured" in comment, (
                "'measurably weaker at small print' is back without a measurement; "
                "the one that exists found identical extraction on 7 posts"
            )
