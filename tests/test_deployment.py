"""The container must not be able to carry a serial number off this machine.

Reference photographs under `knowledge/real_*` and `knowledge/fake` are the
most sensitive thing in the repository: they are photographs of real boxes,
with real serials, and one of them was once committed to a git remote and had
to be purged from history. A distributable image is the same mistake with a
wider blast radius, so the exclusion is enforced here rather than trusted to a
`.dockerignore` nobody reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"

pytestmark = pytest.mark.skipif(not DOCKERFILE.exists(), reason="no container definition")

#: Directories that must never reach a build context.
SECRET_DIRECTORIES = ("knowledge/real_", "knowledge/fake", "corpus/", "results/")


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dockerignore() -> list[str]:
    return [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def test_reference_photographs_are_excluded_from_the_build_context(dockerignore):
    for secret in SECRET_DIRECTORIES:
        assert any(entry.rstrip("/").startswith(secret.rstrip("/")) for entry in dockerignore), (
            f"{secret} is not in .dockerignore; a build would copy real serials into an image"
        )


def test_nothing_un_ignores_a_reference_directory(dockerignore):
    """A later `!pattern` can re-include what an earlier line excluded."""
    for entry in dockerignore:
        if not entry.startswith("!"):
            continue
        for secret in SECRET_DIRECTORIES:
            assert not entry[1:].startswith(secret.rstrip("/")), (
                f"{entry!r} re-includes {secret} after it was excluded"
            )


def test_the_dockerfile_never_copies_a_reference_directory(dockerfile):
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("COPY"):
            continue
        for secret in SECRET_DIRECTORIES:
            assert secret not in stripped, f"{stripped!r} copies {secret} into the image"


def test_apples_font_is_not_redistributed(dockerfile):
    """Bundling SFNS.ttf would breach Apple's font licence."""
    assert "SFNS" not in dockerfile
    assert "/System/Library/Fonts" not in dockerfile


def test_every_copied_path_exists(dockerfile):
    """A COPY of something that is not here fails the build, not the test suite."""
    missing = []
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("COPY ") or "--from=" in stripped:
            continue
        parts = stripped.split()[1:-1]  # drop COPY and the destination
        for source in parts:
            if source.startswith("--"):
                continue
            if any(ch in source for ch in "*?["):
                assert list(ROOT.glob(source)), f"COPY {source} matches nothing"
                continue
            assert (ROOT / source).exists(), f"COPY {source} does not exist"
    assert not missing


def test_the_container_serialises_examinations(dockerfile):
    """Two concurrent vision calls on a free tier make both fail. One at a time."""
    match = re.search(r"EXAMINE_CONCURRENCY=(\d+)", dockerfile)
    assert match is not None, "the image does not pin examination concurrency"
    assert int(match.group(1)) == 1


def test_it_does_not_run_as_root(dockerfile):
    assert re.search(r"^USER\s+\S+", dockerfile, re.M), "the image runs as root"


def test_the_health_check_does_not_spend_quota(dockerfile):
    """A health check that ran an examination would burn the free tier on itself."""
    health = [line for line in dockerfile.splitlines() if "HEALTHCHECK" in line or "CMD node -e" in line]
    joined = " ".join(health)
    if joined:
        assert "/api/examine" in joined and "POST" not in joined
