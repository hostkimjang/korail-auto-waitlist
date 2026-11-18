"""Package existing local images for an explicitly trusted offline installation.

This never builds, tags, pulls or publishes an image, and never reads operator .env files.
Local signing keys are independent of the official release verification key.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import build_bundle

IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
PLATFORM = re.compile(r"^linux/(amd64|arm64)$")
MAX_CONFIG_BYTES = 16 * 1024 * 1024


class Docker:
    def __init__(self, context: str):
        if not context or "\x00" in context:
            raise ValueError("an explicit Docker context is required")
        self.prefix = ["docker", "--context", context]

    def inspect(self, reference: str) -> dict:
        completed = subprocess.run(
            [*self.prefix, "image", "inspect", "--format",
             '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}}}',
             reference],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return json.loads(completed.stdout)

    def save(self, ids: list[str], output: Path) -> None:
        # Saving IDs, rather than names, excludes existing production tags from
        # the transport. Loading this archive must not move those tags elsewhere.
        subprocess.run(
            [*self.prefix, "image", "save", "--output", str(output), *ids],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800,
        )


def file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def source_provenance(root: Path, excluded: tuple[Path, ...] = ()) -> dict:
    """Fingerprint current nonignored source bytes without reading secret inputs.

    The fingerprint identifies the packaging checkout. It does not assert that
    existing Docker images were built from that checkout or from its HEAD.
    """
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("full source revision required")
    names = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True, capture_output=True, timeout=30,
    ).stdout.split(b"\x00")
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        check=True, capture_output=True, timeout=30,
    ).stdout
    digest = hashlib.sha256(b"railwait-local-source-v1\x00")
    for raw_name in sorted(set(filter(None, names))):
        name = os.fsdecode(raw_name)
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe source path")
        # Generated keys and arbitrary user .env variants are not packaging inputs.
        if any(part.startswith(".env") and part != ".env.example" for part in relative.parts):
            continue
        path = root / relative
        if any(path == entry or entry in path.parents for entry in excluded):
            continue
        if path.is_symlink():
            fingerprint = hashlib.sha256(os.fsencode(os.readlink(path))).digest()
            kind = b"link"
        elif path.is_file():
            fingerprint = bytes.fromhex(file_digest(path)[0])
            kind = b"file"
        elif not path.exists():
            fingerprint, kind = b"", b"missing"
        else:
            raise ValueError("unsupported source file")
        digest.update(len(raw_name).to_bytes(8, "big") + raw_name + b"\x00" + kind + fingerprint)
    return {
        "source_revision": revision,
        "source_tree_sha256": digest.hexdigest(),
        "source_dirty": bool(status),
    }


def local_compose_files(root: Path, source_ids: dict[str, str]) -> tuple[dict[str, bytes], dict[str, str]]:
    # Reuse the official builder's bind/maintenance policies, while keeping the
    # local image identity contract separate from registry manifest digests.
    temporary_pins = {
        source: "local.invalid/railwait@" + identifier
        for source, identifier in source_ids.items()
    }
    files, _ = build_bundle.compose_files(root, temporary_pins)
    document = yaml.safe_load(files["compose.release.yml"])
    services = build_bundle.production_compose(root)["services"]
    pinned = {}
    for name, service in document["services"].items():
        image = source_ids[services[name]["image"]]
        service["image"] = image
        service["pull_policy"] = "never"
        pinned[name] = image
    files["compose.release.yml"] = yaml.dump(
        document, Dumper=build_bundle.ResolvedDumper, sort_keys=False, allow_unicode=True,
    ).encode("utf-8")
    return files, pinned


def validate_image_archive(path: Path, expected_ids: set[str], platform: str) -> dict[str, str]:
    """Validate Docker save transport before signing it, including untagged IDs."""
    with tarfile.open(path, "r:") as archive:
        entries = {}
        for member in archive:
            name = member.name.removesuffix("/")
            relative = PurePosixPath(name)
            if (not name or relative.is_absolute() or ".." in relative.parts
                or relative.as_posix() != name or "\\" in name or "\x00" in name
                or ":" in name or name in entries):
                raise ValueError("unsafe Docker archive path")
            if not member.isfile() and not member.isdir():
                raise ValueError("Docker archive links and special files are forbidden")
            entries[name] = member
        manifest_entry = entries.get("manifest.json")
        if not manifest_entry or not manifest_entry.isfile() or manifest_entry.size > MAX_CONFIG_BYTES:
            raise ValueError("Docker save manifest is required")
        manifest = json.load(archive.extractfile(manifest_entry))
        if not isinstance(manifest, list) or not manifest:
            raise ValueError("invalid Docker save manifest")
        actual_ids = set()
        for image in manifest:
            if not isinstance(image, dict) or image.get("RepoTags") not in (None, []):
                raise ValueError("Docker save must not contain production tags")
            config_name = image.get("Config")
            config_entry = entries.get(config_name) if isinstance(config_name, str) else None
            if not config_entry or not config_entry.isfile() or config_entry.size > MAX_CONFIG_BYTES:
                raise ValueError("image configuration missing or too large")
            config = archive.extractfile(config_entry).read()
            identifier = "sha256:" + hashlib.sha256(config).hexdigest()
            if identifier in actual_ids:
                raise ValueError("duplicate Docker image configuration")
            actual_ids.add(identifier)
            parsed = json.loads(config)
            if parsed.get("os") + "/" + parsed.get("architecture") != platform:
                raise ValueError("image archive platform differs")
            layers = image.get("Layers")
            if not isinstance(layers, list) or not all(
                isinstance(name, str) and name in entries and entries[name].isfile()
                for name in layers
            ):
                raise ValueError("image archive layer missing")
        # Classic Docker exposes the config digest as image ID; containerd-backed
        # Docker exposes the OCI index/manifest digest instead. Preserve runtime
        # IDs and bind each to the actual selected config through checked blobs.
        bindings = {}
        index_entry = entries.get("index.json")
        if index_entry is None:
            if actual_ids != expected_ids:
                raise ValueError("image archive does not contain exactly the selected images")
            bindings = {identifier: identifier for identifier in expected_ids}
        else:
            if not index_entry.isfile() or index_entry.size > MAX_CONFIG_BYTES:
                raise ValueError("Docker archive OCI index is required")
            index = json.load(archive.extractfile(index_entry))
            roots = index.get("manifests")
            if not isinstance(roots, list):
                raise ValueError("invalid OCI index roots")
            root_ids = set()
            for descriptor in roots:
                if not isinstance(descriptor, dict):
                    raise ValueError("invalid OCI root descriptor")
                annotations = descriptor.get("annotations", {})
                if not isinstance(annotations, dict) or any(
                    name in annotations for name in ("org.opencontainers.image.ref.name", "io.containerd.image.name")
                ):
                    raise ValueError("OCI archive must not import image names")
                root_ids.add(descriptor.get("digest"))
            if root_ids != expected_ids:
                raise ValueError("OCI archive roots differ from the selected images")

            def checked_blob(identifier, depth=0):
                if not isinstance(identifier, str) or not IMAGE_ID.fullmatch(identifier) or depth > 4:
                    raise ValueError("invalid OCI descriptor")
                entry = entries.get("blobs/sha256/" + identifier[7:])
                if not entry or not entry.isfile() or entry.size > MAX_CONFIG_BYTES:
                    raise ValueError("OCI metadata blob missing or too large")
                data = archive.extractfile(entry).read()
                if "sha256:" + hashlib.sha256(data).hexdigest() != identifier:
                    raise ValueError("OCI metadata blob digest differs")
                document = json.loads(data)
                if "config" in document:
                    return document["config"].get("digest")
                choices = [descriptor for descriptor in document.get("manifests", [])
                           if f"{descriptor.get('platform', {}).get('os')}/{descriptor.get('platform', {}).get('architecture')}" == platform]
                if len(choices) != 1:
                    raise ValueError("OCI index must select exactly one matching CPU")
                return checked_blob(choices[0].get("digest"), depth + 1)

            bindings = {identifier: checked_blob(identifier) for identifier in expected_ids}
        if set(bindings.values()) != actual_ids:
            raise ValueError("image archive does not contain exactly the selected images")
        return bindings


def build_local_package(
    root: Path,
    output: Path,
    version: str,
    platform: str,
    encoded_key: str,
    docker: Docker,
    image_overrides: dict[str, str] | None = None,
    excluded_source_paths: tuple[Path, ...] = (),
) -> str:
    if not build_bundle.VERSION.fullmatch(version) or not PLATFORM.fullmatch(platform):
        raise ValueError("local package needs a semantic version and explicit Linux CPU")
    key_bytes = base64.b64decode(encoded_key.strip(), validate=True)
    if len(key_bytes) != 32:
        raise ValueError("local Ed25519 private seed must contain 32 bytes")
    key = Ed25519PrivateKey.from_private_bytes(key_bytes)
    root, output = root.resolve(), output.absolute()
    originals = build_bundle.source_images(root)
    overrides = image_overrides or {}
    if set(overrides) - set(originals):
        raise ValueError("unknown local image override")
    source_ids = {}
    for original in originals:
        selected = overrides.get(original, original)
        if not isinstance(selected, str) or not selected or selected.startswith("-"):
            raise ValueError("invalid local image reference")
        metadata = docker.inspect(selected)
        identifier = metadata.get("id")
        if not isinstance(identifier, str) or not IMAGE_ID.fullmatch(identifier):
            raise ValueError("local image identity is invalid")
        if f"{metadata.get('os')}/{metadata.get('architecture')}" != platform:
            raise ValueError("all optional-profile images must exist for the selected CPU")
        source_ids[original] = identifier
    files, images = local_compose_files(root, source_ids)
    bundle = build_bundle.archive_bytes(files)
    provenance = source_provenance(root, (*excluded_source_paths, output))
    database_revision = build_bundle.database_head(root)
    output.mkdir(parents=True, exist_ok=False)
    try:
        archive_path = output / "railwait-images.tar"
        docker.save(sorted(set(source_ids.values())), archive_path)
        image_configs = validate_image_archive(archive_path, set(source_ids.values()), platform)
        image_hash, image_size = file_digest(archive_path)
        manifest = {
            "schema_version": 1,
            "kind": "local_package",
            "version": version,
            **provenance,
            "platforms": [platform],
            "minimum_cli_version": "0.1.0",
            "settings_schema": 1,
            "database_revision": database_revision,
            "bundle": {
                "file": "railwait-bundle.tar.gz",
                "sha256": hashlib.sha256(bundle).hexdigest(),
                "size_bytes": len(bundle),
            },
            "image_archive": {
                "file": "railwait-images.tar", "sha256": image_hash, "size_bytes": image_size,
            },
            "images": images,
            "image_config_ids": {service: image_configs[identifier] for service, identifier in images.items()},
            "service_profiles": {
                name: service.get("profiles", [])
                for name, service in build_bundle.production_compose(root)["services"].items()
            },
        }
        payload = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
        (output / "railwait-bundle.tar.gz").write_bytes(bundle)
        (output / "local-manifest.json").write_bytes(payload)
        (output / "local-manifest.sig").write_bytes(base64.b64encode(key.sign(payload)) + b"\n")
    except BaseException:
        # This path was exclusively created by this invocation; never touch any
        # existing output, image, running container, volume, or source checkout.
        shutil.rmtree(output)
        raise
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(public).decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--platform", choices=["linux/amd64", "linux/arm64"], required=True)
    parser.add_argument("--docker-context", required=True)
    parser.add_argument("--images-json", type=Path)
    parser.add_argument("--signing-key-file", type=Path, required=True)
    parser.add_argument("--public-key-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        # File inputs are explicit and excluded from provenance. No official
        # release signing secret or production .env is accepted implicitly.
        if args.public_key_output.exists() or args.public_key_output.is_symlink():
            raise ValueError("public key output already exists")
        public = build_local_package(
            args.root, args.output, args.version, args.platform,
            args.signing_key_file.read_text(encoding="ascii"), Docker(args.docker_context),
            json.loads(args.images_json.read_text(encoding="utf-8")) if args.images_json else {},
            (args.signing_key_file.absolute(), args.public_key_output.absolute()),
        )
        args.public_key_output.write_text(public + "\n", encoding="ascii")
    except Exception:
        print("Local package generation failed. Check local images and inputs; existing images were not modified.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
