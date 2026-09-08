"""Teach Pillow the formats a phone actually produces.

Pillow decodes JPEG, PNG and WebP out of the box. It does NOT decode HEIC,
which is what an iPhone shoots by default — and this tool asks people to
photograph a box with their phone, so that is the single most likely file it
will ever be handed.

The failure was silent in the worst way: the upload control advertised
image/heic, requirements.txt claimed Pillow gave "HEIC/phone-format
normalisation", and the extractor's own docstring said it "handles HEIC and
other phone-camera formats". None of that was true. A real iPhone photograph
came back as "cannot identify image file" — a decoding error, shown to someone
whose photograph was perfectly valid.

Call `register()` before opening a photograph. Every module that opens one
calls it, so there is a single place that can be wrong instead of four. If
pillow-heif is missing the call is a no-op and HEIC stays unsupported, which is
where things already stood rather than an import crash.
"""

from __future__ import annotations

#: True once HEIC/HEIF photographs can actually be decoded. Tests assert on
#: this rather than on whether an import happened to succeed.
HEIF_SUPPORTED = False


def register() -> bool:
    """Register the HEIC decoder with Pillow. Safe to call repeatedly."""
    global HEIF_SUPPORTED
    if HEIF_SUPPORTED:
        return True
    try:
        import pillow_heif
    except ImportError:
        return False
    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
    return True
