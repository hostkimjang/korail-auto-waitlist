"""Build a signed, digest-pinned release without reading the operator's .env."""

from __future__ import annotations

import argparse
import ast
import base64
import copy
import gzip
import hashlib
import io
import json
import os
import re
import sys
import tarfile
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PLATFORMS = ["linux/amd64", "linux/arm64"]
PINNED_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$")
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
# An installation updates only to a release with its own settings schema (and database revision
# and storage images); check_update_sources.py compares the same values.
SETTINGS_SCHEMA = 1
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_FILES = 256


class ResolvedDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def production_compose(root: Path) -> dict:
    document = yaml.safe_load((root / "compose.yml").read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise ValueError("invalid Compose document")
    document = copy.deepcopy(document)
    document = {key: value for key, value in document.items() if not key.startswith("x-")}
    document["services"] = {
        name: service
        for name, service in document["services"].items()
        if "test" not in service.get("profiles", [])
    }
    return document


def source_images(root: Path) -> list[str]:
    return sorted({service["image"] for service in production_compose(root)["services"].values()})


def _asset_files(root: Path, relative: str) -> dict[str, bytes]:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.parts[0] != "infra":
        raise ValueError("unsupported bind source")
    source = root / path
    for candidate in [source, *source.parents]:
        if candidate == root:
            break
        if candidate.is_symlink():
            raise ValueError("symlink assets are forbidden")
    candidates = sorted(source.rglob("*")) if source.is_dir() else [source]
    result = {}
    for candidate in candidates:
        if candidate.is_symlink():
            raise ValueError("symlink assets are forbidden")
        if candidate.is_file():
            result[candidate.relative_to(root).as_posix()] = candidate.read_bytes()
        elif not candidate.is_dir():
            raise ValueError("missing release asset")
    if not result:
        raise ValueError("missing release asset")
    return result


def compose_files(root: Path, images: dict[str, str]) -> tuple[dict[str, bytes], dict[str, str]]:
    document = production_compose(root)
    if (root / ".env.example").is_symlink():
        raise ValueError("symlink assets are forbidden")
    files = {".env.example": (root / ".env.example").read_bytes()}
    pinned = {}
    for name, service in document["services"].items():
        original = service["image"]
        image = images.get(original)
        if not isinstance(image, str) or not PINNED_IMAGE.fullmatch(image):
            raise ValueError("every source image needs a digest pin")
        service["image"] = image
        service.pop("build", None)
        pinned[name] = image
        mounts = []
        for mount in service.get("volumes", []):
            if not isinstance(mount, str):
                raise ValueError("unsupported mount syntax")
            source, separator, target = mount.partition(":")
            if source == "./logs":
                mount = "${RAILWAIT_INSTALL_DIR:?RAILWAIT_INSTALL_DIR must be set}/logs:" + target
            elif source.startswith(".") or source.startswith("/"):
                files.update(_asset_files(root, source.removeprefix("./")))
            mounts.append(mount)
        if "volumes" in service:
            service["volumes"] = mounts
        if original in {"rail-waitlist-api:local", "rail-waitlist-korail-browser-adapter:local"}:
            service.setdefault("environment", {})["RAILWAIT_MANAGEMENT_DIR"] = "/railwait-manager"
            manager_path = "${RAILWAIT_INSTALL_DIR:?RAILWAIT_INSTALL_DIR must be set}/.railwait/manager"
            service.setdefault("volumes", []).extend([
                manager_path + "/control:/railwait-manager/control:ro",
                manager_path + "/gate:/railwait-manager/gate:ro",
            ])
            if name == "api":
                service["environment"]["RAILWAIT_UPDATE_TOKEN_FILE"] = "/railwait-manager/auth/request-token"
                service["volumes"].extend([
                    manager_path + "/auth:/railwait-manager/auth:ro",
                    manager_path + "/status:/railwait-manager/status:ro",
                    manager_path + "/requests:/railwait-manager/requests",
                ])
        if name == "backup":
            service["environment"]["BACKUP_AGE_IDENTITY_BASE64"] = "${BACKUP_AGE_IDENTITY_BASE64:-}"
        for option in service.get("security_opt", []):
            if option.startswith("seccomp:"):
                files.update(_asset_files(root, option.split(":", 1)[1].removeprefix("./")))
    files["compose.release.yml"] = yaml.dump(
        document, Dumper=ResolvedDumper, sort_keys=False, allow_unicode=True
    ).encode("utf-8")
    return files, pinned


def database_head(root: Path) -> str:
    revisions, parents = set(), set()
    for source in (root / "apps/api/migrations/versions").glob("*.py"):
        for statement in ast.parse(source.read_text(encoding="utf-8")).body:
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                        value = ast.literal_eval(statement.value)
                        if target.id == "revision":
                            if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,64}", value):
                                raise ValueError("invalid database revision")
                            revisions.add(value)
                        elif isinstance(value, str):
                            parents.add(value)
                        elif value is not None:
                            raise ValueError("multiple migration parents require explicit release support")
    heads = revisions - parents
    if len(heads) != 1 or not parents.issubset(revisions):
        raise ValueError("release requires a single complete migration chain")
    return heads.pop()


def archive_bytes(files: dict[str, bytes]) -> bytes:
    if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_TOTAL_BYTES:
        raise ValueError("release exceeds extraction bounds")
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, contents in sorted(files.items()):
                if len(contents) > MAX_FILE_BYTES:
                    raise ValueError("release file exceeds extraction bound")
                path = Path(name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("unsafe archive path")
                entry = tarfile.TarInfo(name)
                entry.size, entry.mode, entry.mtime = len(contents), 0o644, 0
                archive.addfile(entry, io.BytesIO(contents))
    return output.getvalue()


def build_release(
    root: Path,
    images: dict[str, str],
    output: Path,
    version: str,
    source_revision: str,
    minimum_cli_version: str,
    encoded_key: str,
    repository: str = "hostkimjang/korail-auto-waitlist",
    compatible_from: tuple[str, ...] = (),
) -> str:
    if not VERSION.fullmatch(version) or not VERSION.fullmatch(minimum_cli_version):
        raise ValueError("stable semantic version required")
    if not re.fullmatch(r"[0-9a-f]{40}", source_revision):
        raise ValueError("full source revision required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid repository")
    for source in compatible_from:
        if not VERSION.fullmatch(source) or tuple(map(int, source.split("."))) >= tuple(map(int, version.split("."))):
            raise ValueError("invalid compatible source version")
    key_bytes = base64.b64decode(encoded_key.strip(), validate=True)
    if len(key_bytes) != 32:
        raise ValueError("Ed25519 private seed must contain 32 bytes")
    key = Ed25519PrivateKey.from_private_bytes(key_bytes)
    files, pinned = compose_files(root.resolve(), images)
    bundle = archive_bytes(files)
    manifest = {
        "schema_version": 1,
        "version": version,
        "channel": "stable",
        "source_revision": source_revision,
        "platforms": PLATFORMS,
        "minimum_cli_version": minimum_cli_version,
        "database_revision": database_head(root),
        "settings_schema": SETTINGS_SCHEMA,
        "compatible_from": list(compatible_from),
        "bundle": {
            "url": f"https://github.com/{repository}/releases/download/v{version}/railwait-bundle.tar.gz",
            "sha256": hashlib.sha256(bundle).hexdigest(),
            "size_bytes": len(bundle),
        },
        "images": pinned,
        "service_profiles": {
            name: service.get("profiles", [])
            for name, service in production_compose(root)["services"].items()
        },
    }
    payload = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
    assets = {
        "railwait-bundle.tar.gz": bundle,
        "release-manifest.json": payload,
        "release-manifest.sig": base64.b64encode(key.sign(payload)) + b"\n",
    }
    output.mkdir(parents=True, exist_ok=False)
    for name, contents in assets.items():
        (output / name).write_bytes(contents)
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(public).decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--list-images", action="store_true")
    parser.add_argument("--images-json", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--source-revision")
    parser.add_argument("--minimum-cli-version", default="0.1.0")
    parser.add_argument("--compatible-from", action="append", default=[])
    parser.add_argument("--repository", default="hostkimjang/korail-auto-waitlist")
    parser.add_argument("--signing-key-file", type=Path)
    parser.add_argument("--public-key-output", type=Path)
    args = parser.parse_args()
    try:
        if args.list_images:
            print(json.dumps(source_images(args.root)))
            return 0
        if not all([args.images_json, args.output, args.version, args.source_revision]):
            raise ValueError("missing release arguments")
        key = (
            args.signing_key_file.read_text(encoding="ascii")
            if args.signing_key_file
            else os.environ.get("RAILWAIT_RELEASE_SIGNING_KEY", "")
        )
        public = build_release(
            args.root,
            json.loads(args.images_json.read_text(encoding="utf-8")),
            args.output,
            args.version,
            args.source_revision,
            args.minimum_cli_version,
            key,
            args.repository,
            tuple(args.compatible_from),
        )
        if args.public_key_output:
            args.public_key_output.write_text(public + "\n", encoding="ascii")
    except Exception:
        print(
            "Release bundle generation failed. Check inputs and signing configuration.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
