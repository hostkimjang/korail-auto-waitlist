"""Resolve and validate multi-platform registry pins before signing a release."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from build_bundle import PINNED_IMAGE, PLATFORMS, source_images


def inspect_image(reference: str) -> dict:
    completed = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", reference, "--format", "{{json .Manifest}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(completed.stdout)


def resolve_images(
    originals: list[str], built: dict[str, str], inspect=inspect_image
) -> dict[str, str]:
    result = {}
    for original in originals:
        reference = built.get(original, original)
        manifest = inspect(reference)
        digest = manifest.get("digest", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("invalid registry digest")
        platforms = {
            f"{entry.get('platform', {}).get('os')}/{entry.get('platform', {}).get('architecture')}"
            for entry in manifest.get("manifests", [])
        }
        if not set(PLATFORMS).issubset(platforms):
            raise ValueError("registry image lacks a required platform")
        pin = reference.split("@", 1)[0] + "@" + digest
        if not PINNED_IMAGE.fullmatch(pin):
            raise ValueError("invalid pinned image")
        if original in built and reference != pin:
            raise ValueError("built image digest changed")
        result[original] = pin
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--built-images-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    built = {}
    for path in sorted(args.built_images_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        original, pinned = record["original"], record["pinned"]
        if original in built or not PINNED_IMAGE.fullmatch(pinned):
            raise ValueError("invalid or duplicate built image record")
        built[original] = pinned
    originals = source_images(args.root)
    if set(built) - set(originals):
        raise ValueError("unknown built image record")
    args.output.write_text(
        json.dumps(resolve_images(originals, built), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
