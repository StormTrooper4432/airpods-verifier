"""The eBay parser, exercised without a key.

Every field name in `fetch_ebay` was written against eBay's published Browse
API documentation, and documentation drifts. These fix the shape this tool
expects, so a change shows up as a failing test rather than as a corpus run
that quietly collects nothing. `--probe` covers the other half: one live call
that reports what the API actually returned.
"""

from __future__ import annotations

import pytest

from tools import fetch_ebay


#: The documented getItem response shape, trimmed to the fields read here.
#: https://developer.ebay.com/api-docs/buy/browse/resources/item/methods/getItem
ITEM_RESPONSE = {
    "itemId": "v1|1234567890|0",
    "title": "Apple AirPods Pro 2nd Generation - Used",
    "condition": "Used",
    "conditionId": "3000",
    "price": {"value": "142.50", "currency": "USD"},
    "seller": {"username": "a_seller", "feedbackPercentage": "99.3"},
    "itemWebUrl": "https://www.ebay.com/itm/1234567890",
    "image": {"imageUrl": "https://i.ebayimg.com/images/g/AAA/s-l1600.jpg"},
    "additionalImages": [
        {"imageUrl": "https://i.ebayimg.com/images/g/BBB/s-l1600.jpg"},
        {"imageUrl": "https://i.ebayimg.com/images/g/CCC/s-l1600.jpg"},
    ],
}

SEARCH_RESPONSE = {
    "href": "https://api.ebay.com/buy/browse/v1/item_summary/search?q=airpods",
    "total": 2,
    "itemSummaries": [
        {"itemId": "v1|1111|0", "title": "one"},
        {"itemId": "v1|2222|0", "title": "two"},
    ],
}


class TestParsing:
    def test_it_reads_the_documented_shape(self):
        listing = fetch_ebay.parse_listing("v1|1234567890|0", ITEM_RESPONSE)
        assert listing is not None
        assert listing.title.startswith("Apple AirPods Pro")
        assert listing.condition == "Used"
        assert listing.price == "142.50 USD"
        assert listing.seller == "a_seller"
        assert listing.url.endswith("/itm/1234567890")
        assert len(listing.image_urls) == 3
        assert listing.image_urls[0].endswith("AAA/s-l1600.jpg")

    def test_the_primary_image_is_not_counted_twice(self):
        """eBay sometimes repeats the primary image at the head of the extras."""
        payload = dict(ITEM_RESPONSE)
        payload["additionalImages"] = [
            {"imageUrl": ITEM_RESPONSE["image"]["imageUrl"]},
            *ITEM_RESPONSE["additionalImages"],
        ]
        listing = fetch_ebay.parse_listing("x", payload)
        assert listing is not None
        assert len(listing.image_urls) == len(set(listing.image_urls)) == 3

    def test_a_listing_with_no_photographs_is_skipped(self):
        """No images means nothing to examine, whatever else the listing says."""
        payload = {k: v for k, v in ITEM_RESPONSE.items() if k not in ("image", "additionalImages")}
        assert fetch_ebay.parse_listing("x", payload) is None

    def test_a_missing_optional_field_does_not_lose_the_listing(self):
        """Photographs are the point; price and seller are context."""
        payload = {k: v for k, v in ITEM_RESPONSE.items() if k not in ("price", "seller")}
        listing = fetch_ebay.parse_listing("x", payload)
        assert listing is not None
        assert listing.image_urls
        assert listing.price == ""
        assert listing.seller == ""

    def test_malformed_image_entries_are_stepped_over(self):
        payload = dict(ITEM_RESPONSE)
        payload["additionalImages"] = [
            {"imageUrl": "https://i.ebayimg.com/images/g/DDD/s-l1600.jpg"},
            {"noUrlHere": True},
            "not-a-dict",
            None,
        ]
        listing = fetch_ebay.parse_listing("x", payload)
        assert listing is not None
        assert len(listing.image_urls) == 2


class TestSearchResults:
    def test_it_reads_item_ids(self):
        assert fetch_ebay.item_ids_from(SEARCH_RESPONSE) == ["v1|1111|0", "v1|2222|0"]

    def test_it_falls_back_to_the_legacy_id(self):
        payload = {"itemSummaries": [{"legacyItemId": "1234567890"}]}
        assert fetch_ebay.item_ids_from(payload) == ["1234567890"]

    def test_an_empty_page_yields_nothing_rather_than_raising(self):
        assert fetch_ebay.item_ids_from({}) == []
        assert fetch_ebay.item_ids_from({"itemSummaries": []}) == []


class TestTermsOfUse:
    """eBay's robots.txt prohibits automated access to the site and directs
    integrations to the Browse API. This tool must never grow a scraper."""

    def test_it_only_talks_to_the_api_host(self):
        source = (fetch_ebay.__file__ and open(fetch_ebay.__file__, encoding="utf-8").read()) or ""
        for host in ("www.ebay.com/sch", "www.ebay.com/itm", "ebay.com/b/"):
            assert host not in source, f"{host} is a site URL, not the API"
        assert fetch_ebay.BROWSE_URL.startswith("https://api.ebay.com/")
        assert fetch_ebay.ITEM_URL.startswith("https://api.ebay.com/")
        assert fetch_ebay.OAUTH_URL.startswith("https://api.ebay.com/")

    def test_requests_are_paced(self):
        """A burst is what gets a key throttled, and the allowance is shared."""
        assert fetch_ebay.PAUSE_SECONDS > 0

    def test_the_page_size_stays_inside_the_documented_maximum(self):
        """Browse caps `limit` at 200 per page."""
        import inspect

        assert "min(200," in inspect.getsource(fetch_ebay.search)


def test_no_key_is_a_clear_message_not_a_traceback(capsys, monkeypatch):
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    assert fetch_ebay.main(["--limit", "1"]) == 2
    captured = capsys.readouterr()
    assert "developer.ebay.com" in captured.err
    assert "EBAY_CLIENT_ID" in captured.err


@pytest.mark.parametrize("field", fetch_ebay.EXPECTED_ITEM_FIELDS)
def test_every_probed_field_is_one_the_parser_actually_reads(field):
    """`--probe` should report on the fields this tool depends on, no others."""
    import inspect

    source = inspect.getsource(fetch_ebay.parse_listing)
    assert f'"{field}"' in source, f"--probe checks {field}, but parse_listing never reads it"
