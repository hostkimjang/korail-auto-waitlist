"""Refuse a release whose approved source releases could not update to it.

An installation updates only to a release with its own database revision, settings schema and
PostgreSQL and Redis images (apps/cli/internal/install/update.go). Every version passed as
--compatible-from must therefore match this release on all four, as its published manifest
records them. Without --images-json the storage images are read from compose.yml, where they must
be pinned by digest, so the list can be checked before any image is built.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path

from build_bundle import (
    PINNED_IMAGE,
    SETTINGS_SCHEMA,
    VERSION,
    database_head,
    production_compose,
)

STORAGE_SERVICES = ("postgres", "redis")
MAX_MANIFEST_BYTES = 1024 * 1024

Manifest = dict[str, object]
Fetch = Callable[[str, str], Manifest]


def update_contract(root: Path, images: dict[str, str] | None = None) -> dict[str, object]:
    """Values an installation must share with this release to update to it."""
    services = production_compose(root)["services"]
    contract: dict[str, object] = {
        "database_revision": database_head(root),
        "settings_schema": SETTINGS_SCHEMA,
    }
    for name in STORAGE_SERVICES:
        original = services[name]["image"]
        image = original if images is None else images.get(original)
        if not isinstance(image, str) or not PINNED_IMAGE.fullmatch(image):
            raise ValueError(f"the {name} image must be pinned by digest in compose.yml")
        contract[f"images.{name}"] = image
    return contract


def source_contract(manifest: Manifest) -> dict[str, object]:
    images = manifest.get("images")
    images = images if isinstance(images, dict) else {}
    contract: dict[str, object] = {
        "database_revision": manifest.get("database_revision"),
        "settings_schema": manifest.get("settings_schema"),
    }
    for name in STORAGE_SERVICES:
        contract[f"images.{name}"] = images.get(name)
    return contract


def fetch_manifest(repository: str, version: str) -> Manifest:
    url = f"https://github.com/{repository}/releases/download/v{version}/release-manifest.json"
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest exceeds its size bound")
    manifest = json.loads(data)
    if not isinstance(manifest, dict):
        raise TypeError("manifest is not an object")
    return manifest


def incompatible_sources(
    target: dict[str, object], sources: Iterable[str], fetch: Callable[[str], Manifest]
) -> list[str]:
    """Describe every approved source release that could not update to target."""
    problems = []
    for version in sources:
        if not VERSION.fullmatch(version):
            problems.append(f"{version!r}: not a stable version")
            continue
        try:
            manifest = fetch(version)
        except (OSError, TypeError, ValueError) as error:
            problems.append(f"{version}: published manifest unavailable ({error})")
            continue
        if manifest.get("version") != version:
            problems.append(f"{version}: published manifest names {manifest.get('version')!r}")
            continue
        source = source_contract(manifest)
        for key, value in target.items():
            if source[key] != value:
                problems.append(f"{version}: {key} {source[key]!r} differs from {value!r}")
    return problems


def main(argv: list[str] | None = None, fetch: Fetch = fetch_manifest) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--images-json", type=Path)
    parser.add_argument("--compatible-from", action="append", default=[])
    parser.add_argument("--repository", default="hostkimjang/korail-auto-waitlist")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        raise ValueError("invalid repository")
    images = None
    if args.images_json is not None:
        images = json.loads(args.images_json.read_text(encoding="utf-8"))
    target = update_contract(args.root, images)
    problems = incompatible_sources(
        target, args.compatible_from, lambda version: fetch(args.repository, version)
    )
    if problems:
        print(
            "compatible_from lists releases that could not update to this release:",
            file=sys.stderr,
        )
        for problem in problems:
            print("  " + problem, file=sys.stderr)
        print(
            "Remove them from compatible_from, or keep the database revision, settings schema and"
            " storage images they were published with.",
            file=sys.stderr,
        )
        return 1
    print(f"{len(args.compatible_from)} approved source release(s) can update to this release")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
