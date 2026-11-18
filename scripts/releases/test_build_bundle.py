from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_bundle
import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[2]
KEY = base64.b64encode(bytes(range(32))).decode("ascii")


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="railwait-release-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.images = {
            original: f"registry.example/release/image-{index}@sha256:{index:064x}"
            for index, original in enumerate(build_bundle.source_images(ROOT), 1)
        }

    def build(self, output="release", root=ROOT):
        target = self.path / output
        public = build_bundle.build_release(
            root, self.images, target, "1.4.0", "a" * 40, "0.1.0", KEY
        )
        return target, public

    def files(self, target):
        with tarfile.open(target / "railwait-bundle.tar.gz", "r:gz") as archive:
            entries = archive.getmembers()
            self.assertTrue(all(entry.isfile() for entry in entries))
            self.assertTrue(all(entry.mode == 0o644 for entry in entries))
            return {entry.name: archive.extractfile(entry).read() for entry in entries}

    def test_manifest_signature_and_deterministic_archive(self):
        target, encoded_public = self.build()
        second, _ = self.build("repeat")
        self.assertEqual(
            {p.name: p.read_bytes() for p in target.iterdir()},
            {p.name: p.read_bytes() for p in second.iterdir()},
        )
        payload = (target / "release-manifest.json").read_bytes()
        signature = base64.b64decode((target / "release-manifest.sig").read_bytes())
        public = Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded_public))
        public.verify(signature, payload)
        with self.assertRaises(InvalidSignature):
            public.verify(signature, payload + b" ")
        manifest = json.loads(payload)
        bundle = (target / "railwait-bundle.tar.gz").read_bytes()
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["settings_schema"], 1)
        self.assertEqual(manifest["database_revision"], build_bundle.database_head(ROOT))
        self.assertEqual(manifest["compatible_from"], [])
        self.assertEqual(manifest["platforms"], ["linux/amd64", "linux/arm64"])
        self.assertEqual(set(manifest["service_profiles"]), set(manifest["images"]))
        self.assertEqual(manifest["service_profiles"]["api"], [])
        self.assertEqual(
            manifest["service_profiles"]["korail-browser-adapter"], ["experimental-rail"]
        )
        self.assertEqual(manifest["bundle"]["sha256"], hashlib.sha256(bundle).hexdigest())
        self.assertEqual(manifest["bundle"]["size_bytes"], len(bundle))
        self.assertEqual(
            manifest["bundle"]["url"],
            "https://github.com/hostkimjang/korail-auto-waitlist/releases/download/v1.4.0/railwait-bundle.tar.gz",
        )
        self.assertNotIn(KEY.encode(), payload + bundle)

    def test_compose_preserves_production_contract_and_all_pins(self):
        target, _ = self.build()
        files = self.files(target)
        document = yaml.safe_load(files["compose.release.yml"])
        expected = build_bundle.production_compose(ROOT)
        self.assertNotIn("korail-browser-adapter-test", document["services"])
        self.assertEqual(document["volumes"], expected["volumes"])
        self.assertEqual(document["networks"], expected["networks"])
        self.assertIn("korail_browser_profile", document["volumes"])
        self.assertFalse(any(key.startswith("x-") for key in document))
        self.assertNotIn(b"&id", files["compose.release.yml"])
        manifest = json.loads((target / "release-manifest.json").read_bytes())
        self.assertEqual(set(manifest["images"]), set(expected["services"]))
        for name, service in expected["services"].items():
            original = copy.deepcopy(service)
            original.pop("build", None)
            original["image"] = self.images[service["image"]]
            original["volumes"] = [
                mount.replace(
                    "./logs:",
                    "${RAILWAIT_INSTALL_DIR:?RAILWAIT_INSTALL_DIR must be set}/logs:",
                    1,
                )
                for mount in original.get("volumes", [])
            ]
            if "volumes" not in service:
                original.pop("volumes")
            if service["image"] in {"rail-waitlist-api:local", "rail-waitlist-korail-browser-adapter:local"}:
                original.setdefault("environment", {})["RAILWAIT_MANAGEMENT_DIR"] = "/railwait-manager"
                base = "${RAILWAIT_INSTALL_DIR:?RAILWAIT_INSTALL_DIR must be set}/.railwait/manager"
                original.setdefault("volumes", []).extend([
                    base + "/control:/railwait-manager/control:ro",
                    base + "/gate:/railwait-manager/gate:ro",
                ])
                if name == "api":
                    original["environment"]["RAILWAIT_UPDATE_TOKEN_FILE"] = "/railwait-manager/auth/request-token"
                    original["volumes"].extend([
                        base + "/auth:/railwait-manager/auth:ro",
                        base + "/status:/railwait-manager/status:ro",
                        base + "/requests:/railwait-manager/requests",
                    ])
            if name == "backup":
                original["environment"]["BACKUP_AGE_IDENTITY_BASE64"] = "${BACKUP_AGE_IDENTITY_BASE64:-}"
            self.assertEqual(document["services"][name], original, name)
            self.assertEqual(document["services"][name]["image"], manifest["images"][name])
        self.assertEqual(
            set(files),
            {
                "compose.release.yml",
                ".env.example",
                "infra/caddy/Caddyfile",
                "infra/redis/provider-admission-entrypoint.sh",
                "infra/docker/playwright-seccomp-v1.55.0.json",
                "infra/prometheus/prometheus.yml",
                "infra/grafana/provisioning/datasources/prometheus.yml",
            },
        )
        for name in files:
            if name != "compose.release.yml":
                self.assertEqual(files[name], (ROOT / name).read_bytes())

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose unavailable")
    def test_compose_config_with_all_production_profiles_and_isolated_env(self):
        target, _ = self.build()
        extracted = self.path / "extracted"
        extracted.mkdir()
        for name, content in self.files(target).items():
            destination = extracted / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        envfile = self.path / "release-test.env"
        envfile.write_text(
            "POSTGRES_PASSWORD=release-test-value\n"
            "SECRET_ENCRYPTION_KEY=release-test-key-value-not-a-secret\n"
            "AUTH_SESSION_SECRET=release-test-session-value-not-a-secret\n"
            f"RAILWAIT_INSTALL_DIR={self.path.as_posix()}\n",
            encoding="utf-8",
        )
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"}
        }
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(envfile),
                "-f",
                str(extracted / "compose.release.yml"),
                "--profile",
                "*",
                "config",
                "--quiet",
            ],
            cwd=self.path,
            env=environment,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_compatible_sources_are_explicit_and_older(self):
        target = self.path / "approved"
        build_bundle.build_release(
            ROOT, self.images, target, "1.4.0", "a" * 40, "0.1.0", KEY,
            compatible_from=("1.3.3",),
        )
        self.assertEqual(json.loads((target / "release-manifest.json").read_bytes())["compatible_from"], ["1.3.3"])
        for source in ("1.4.0", "2.0.0", "v1.3.3", "../invalid"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                build_bundle.build_release(
                    ROOT, self.images, self.path / "rejected", "1.4.0", "a" * 40,
                    "0.1.0", KEY, compatible_from=(source,),
                )

    def test_missing_or_mutable_image_pin_is_rejected_before_output(self):
        original = next(iter(self.images))
        for value in [None, "registry.example/image:latest", "registry.example/image@sha256:short"]:
            with self.subTest(value=value):
                changed = dict(self.images)
                if value is None:
                    changed.pop(original)
                else:
                    changed[original] = value
                with self.assertRaises(ValueError):
                    build_bundle.build_release(
                        ROOT, changed, self.path / "invalid", "1.4.0", "a" * 40, "0.1.0", KEY
                    )
                self.assertFalse((self.path / "invalid").exists())

    def fixture_root(self):
        fixture = self.path / "source"
        fixture.mkdir()
        (fixture / ".env.example").write_text("EXAMPLE=\n", encoding="utf-8")
        (fixture / "compose.yml").write_text(
            "services:\n  app:\n    image: example:local\n"
            "    volumes:\n      - ./infra/asset:/asset:ro\n",
            encoding="utf-8",
        )
        self.images = {"example:local": "registry.example/app@sha256:" + "1" * 64}
        return fixture

    def test_symlink_asset_is_rejected(self):
        fixture = self.fixture_root()
        (fixture / "infra").mkdir()
        (fixture / "infra/asset").symlink_to(ROOT / ".env.example")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.build(root=fixture)

    def test_unexpected_host_bind_is_rejected(self):
        fixture = self.fixture_root()
        compose = fixture / "compose.yml"
        compose.write_text(compose.read_text().replace("./infra/asset", "../private"))
        with self.assertRaisesRegex(ValueError, "unsupported bind"):
            self.build(root=fixture)

    def test_environment_template_symlink_is_rejected(self):
        fixture = self.fixture_root()
        template = fixture / ".env.example"
        template.unlink()
        template.symlink_to(ROOT / ".env.example")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.build(root=fixture)

    def test_cli_error_does_not_echo_signing_key(self):
        errors = io.StringIO()
        with patch("sys.argv", ["build_bundle.py", "--version", "1.4.0"]):
            with patch.dict(os.environ, {"RAILWAIT_RELEASE_SIGNING_KEY": "invalid-private-key"}):
                with patch("sys.stderr", errors):
                    self.assertEqual(build_bundle.main(), 1)
        self.assertEqual(
            errors.getvalue(),
            "Release bundle generation failed. Check inputs and signing configuration.\n",
        )

    def test_archive_rejects_unsafe_paths_and_size_bounds(self):
        with self.assertRaises(ValueError):
            build_bundle.archive_bytes({"../escape": b"x"})
        with self.assertRaises(ValueError):
            build_bundle.archive_bytes(
                {"compose.release.yml": b"x" * (build_bundle.MAX_FILE_BYTES + 1)}
            )


if __name__ == "__main__":
    unittest.main()
