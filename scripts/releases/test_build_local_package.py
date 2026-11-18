from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import build_bundle
import build_local_package as local

ROOT = Path(__file__).resolve().parents[2]
KEY = base64.b64encode(bytes(range(32))).decode("ascii")


class FakeDocker:
    def __init__(self, root: Path):
        self.configs = {}
        self.references = {}
        self.inspected = []
        self.saved = []
        self.tags = None
        self.fail_save = False
        for original in build_bundle.source_images(root):
            config = json.dumps({"os": "linux", "architecture": "arm64", "fixture": original}).encode()
            identifier = "sha256:" + hashlib.sha256(config).hexdigest()
            self.configs[identifier] = config
            self.references[original] = identifier

    def inspect(self, reference):
        self.inspected.append(reference)
        if reference not in self.references:
            raise ValueError("image missing")
        return {"id": self.references[reference], "os": "linux", "architecture": "arm64"}

    def save(self, ids, output):
        self.saved.append(ids)
        if self.fail_save:
            output.write_bytes(b"partial download")
            raise ValueError("save failed")
        entries = {}
        manifest = []
        for identifier in ids:
            config = self.configs[identifier]
            name = "blobs/sha256/" + identifier.removeprefix("sha256:")
            entries[name] = config
            manifest.append({"Config": name, "RepoTags": self.tags, "Layers": []})
        entries["manifest.json"] = json.dumps(manifest).encode()
        with tarfile.open(output, "w") as archive:
            for name, data in entries.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))


class LocalPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="railwait-local-package-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.root = self.path / "source"
        self.root.mkdir()
        for relative in ["compose.yml", ".env.example"]:
            shutil.copyfile(ROOT / relative, self.root / relative)
        shutil.copytree(ROOT / "infra", self.root / "infra")
        migrations = self.root / "apps/api/migrations/versions"
        migrations.mkdir(parents=True)
        (migrations / "0001.py").write_text("revision = 'local_test'\ndown_revision = None\n")
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "-C", self.root, "add", "."], check=True)
        subprocess.run(
            ["git", "-C", self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "-qm", "fixture"], check=True,
        )
        self.docker = FakeDocker(self.root)

    def build(self, output="package", **kwargs):
        target = self.path / output
        public = local.build_local_package(
            self.root, target, "1.4.0", "linux/arm64", KEY, self.docker, **kwargs,
        )
        return target, public

    def test_signed_manifest_transport_and_offline_compose(self):
        output, public = self.build()
        payload = (output / "local-manifest.json").read_bytes()
        signature = base64.b64decode((output / "local-manifest.sig").read_bytes())
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(signature, payload)
        manifest = json.loads(payload)
        self.assertEqual(set(p.name for p in output.iterdir()), {
            "local-manifest.json", "local-manifest.sig", "railwait-bundle.tar.gz", "railwait-images.tar",
        })
        self.assertEqual(manifest["kind"], "local_package")
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["platforms"], ["linux/arm64"])
        self.assertFalse(manifest["source_dirty"])
        self.assertRegex(manifest["source_tree_sha256"], "^[a-f0-9]{64}$")
        self.assertEqual(manifest["database_revision"], "local_test")
        services = build_bundle.production_compose(self.root)["services"]
        self.assertEqual(set(manifest["images"]), set(services))
        self.assertEqual(manifest["image_config_ids"], manifest["images"])
        self.assertEqual(set(manifest["service_profiles"]), set(services))
        for field in ("bundle", "image_archive"):
            digest, size = local.file_digest(output / manifest[field]["file"])
            self.assertEqual((digest, size), (manifest[field]["sha256"], manifest[field]["size_bytes"]))
        with tarfile.open(output / "railwait-bundle.tar.gz") as bundle:
            document = yaml.safe_load(bundle.extractfile("compose.release.yml").read())
            for name, service in document["services"].items():
                self.assertNotIn("build", service)
                self.assertEqual(service["pull_policy"], "never")
                self.assertEqual(service["image"], manifest["images"][name])
            self.assertFalse(any(member.name == ".env" for member in bundle))
        self.assertEqual(self.docker.saved, [sorted(set(self.docker.references.values()))])
        self.assertEqual(set(self.docker.inspected), set(build_bundle.source_images(self.root)))
        self.assertNotIn(KEY.encode(), payload)

    def test_local_signing_inputs_and_env_are_excluded_from_source_fingerprint(self):
        initial = local.source_provenance(self.root)
        (self.root / ".env").write_text("PRIVATE_FIXTURE_DO_NOT_READ=fixture\n")
        (self.root / ".env.backup").write_text("PRIVATE_FIXTURE_DO_NOT_READ=another\n")
        (self.root / ".env.bakdir").mkdir()
        (self.root / ".env.bakdir/secret").write_text("fixture")
        key = self.root / "local-signing-key"
        key.write_text(KEY)
        original_digest = local.file_digest

        def guarded(path):
            self.assertNotIn(".env", [part for part in path.parts if part != ".env.example"])
            self.assertNotEqual(path, key)
            return original_digest(path)

        with patch.object(local, "file_digest", guarded):
            changed = local.source_provenance(self.root, (key,))
        self.assertEqual(initial["source_tree_sha256"], changed["source_tree_sha256"])
        self.assertTrue(changed["source_dirty"])
        with (self.root / "compose.yml").open("a") as stream:
            stream.write("\n# modified packaging source\n")
        actual = local.source_provenance(self.root, (key,))
        self.assertNotEqual(initial["source_tree_sha256"], actual["source_tree_sha256"])
        self.assertEqual(initial["source_revision"], actual["source_revision"])

    def test_source_symlink_is_fingerprinted_without_reading_external_target(self):
        target = self.path / "outside-secret"
        target.write_text("fixture one")
        (self.root / "external-link").symlink_to(target)
        first = local.source_provenance(self.root)
        target.write_text("fixture two")
        self.assertEqual(first, local.source_provenance(self.root))

    def test_all_optional_profile_images_must_exist_before_output_is_created(self):
        del self.docker.references["grafana/grafana:11.3.1"]
        with self.assertRaisesRegex(ValueError, "missing"):
            self.build()
        self.assertFalse((self.path / "package").exists())
        self.assertEqual(self.docker.saved, [])

    def test_wrong_cpu_is_rejected_without_saving(self):
        self.docker.inspect = lambda reference: {"id": "sha256:" + "a" * 64, "os": "linux", "architecture": "amd64"}
        with self.assertRaisesRegex(ValueError, "CPU"):
            self.build()
        self.assertFalse((self.path / "package").exists())

    def test_archive_cannot_import_production_tags(self):
        self.docker.tags = ["rail-waitlist-api:local"]
        with self.assertRaisesRegex(ValueError, "tags"):
            self.build()
        self.assertFalse((self.path / "package").exists())

    def test_failed_save_cleans_only_new_output(self):
        preserved = self.path / "existing"
        preserved.mkdir()
        (preserved / "important").write_text("preserve")
        self.docker.fail_save = True
        with self.assertRaisesRegex(ValueError, "save failed"):
            self.build()
        self.assertFalse((self.path / "package").exists())
        self.assertEqual((preserved / "important").read_text(), "preserve")
        with self.assertRaises(FileExistsError):
            self.build(output="existing")
        self.assertEqual((preserved / "important").read_text(), "preserve")

    def test_override_can_select_new_isolated_images_without_moving_old_tags(self):
        original = "rail-waitlist-api:local"
        selected = "railwait-validation-api:unique"
        self.docker.references[selected] = self.docker.references[original]
        before = dict(self.docker.references)
        self.build(image_overrides={original: selected})
        self.assertIn(selected, self.docker.inspected)
        self.assertNotIn(original, self.docker.inspected)
        self.assertEqual(before, self.docker.references)

    def test_archive_missing_or_extra_configuration_is_rejected(self):
        output, _ = self.build()
        with self.assertRaisesRegex(ValueError, "exactly"):
            local.validate_image_archive(output / "railwait-images.tar", set(), "linux/arm64")

    def oci_archive(self, *, annotation=None, tamper=False):
        config = b'{"os":"linux","architecture":"arm64"}'
        config_id = "sha256:" + hashlib.sha256(config).hexdigest()
        document = json.dumps({"config": {"digest": config_id}, "layers": []}).encode()
        manifest_id = "sha256:" + hashlib.sha256(document).hexdigest()
        index = json.dumps({"manifests": [
            {"digest": manifest_id, "platform": {"os": "linux", "architecture": "arm64"}},
            {"digest": "sha256:" + "a" * 64, "platform": {"os": "linux", "architecture": "amd64"}},
            {"digest": "sha256:" + "b" * 64, "platform": {"os": "unknown", "architecture": "unknown"}},
        ]}).encode()
        runtime_id = "sha256:" + hashlib.sha256(index).hexdigest()
        root = {"manifests": [{"digest": runtime_id, "annotations": annotation or {}}]}
        files = {
            "manifest.json": json.dumps([{"Config": "blobs/sha256/" + config_id[7:], "RepoTags": None, "Layers": []}]).encode(),
            "index.json": json.dumps(root).encode(),
            "blobs/sha256/" + config_id[7:]: config,
            "blobs/sha256/" + manifest_id[7:]: document + (b" " if tamper else b""),
            "blobs/sha256/" + runtime_id[7:]: index,
        }
        target = self.path / "oci.tar"
        with tarfile.open(target, "w") as archive:
            for name, data in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        return target, runtime_id, config_id

    def test_containerd_index_binds_native_platform_configuration(self):
        target, runtime_id, config_id = self.oci_archive()
        self.assertNotEqual(runtime_id, config_id)
        self.assertEqual(local.validate_image_archive(target, {runtime_id}, "linux/arm64"), {runtime_id: config_id})
        with self.assertRaisesRegex(ValueError, "platform"):
            local.validate_image_archive(target, {runtime_id}, "linux/amd64")

    def test_containerd_metadata_tampering_and_name_import_are_rejected(self):
        target, runtime_id, _ = self.oci_archive(tamper=True)
        with self.assertRaisesRegex(ValueError, "digest"):
            local.validate_image_archive(target, {runtime_id}, "linux/arm64")
        target, runtime_id, _ = self.oci_archive(annotation={"org.opencontainers.image.ref.name": "api:local"})
        with self.assertRaisesRegex(ValueError, "names"):
            local.validate_image_archive(target, {runtime_id}, "linux/arm64")

    def test_classic_config_ids_cannot_bypass_oci_names_check(self):
        target, _, config_id = self.oci_archive(annotation={"org.opencontainers.image.ref.name": "api:local"})
        with self.assertRaisesRegex(ValueError, "names"):
            local.validate_image_archive(target, {config_id}, "linux/arm64")


if __name__ == "__main__":
    unittest.main()
