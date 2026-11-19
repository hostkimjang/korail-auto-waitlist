from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from unittest import mock

import check_update_sources as check
from build_bundle import PINNED_IMAGE, production_compose

ROOT = Path(__file__).resolve().parents[2]
FIRST_BUILD = "postgres:16-alpine@sha256:" + "a" * 64
NEWER_BUILD = "postgres:16-alpine@sha256:" + "b" * 64
REDIS = "redis:7.4-alpine@sha256:" + "c" * 64
TARGET = {
    "database_revision": "0042_protection_halt",
    "settings_schema": 1,
    "images.postgres": FIRST_BUILD,
    "images.redis": REDIS,
}


def published(version, postgres=FIRST_BUILD, revision="0042_protection_halt", settings=1):
    return {
        "version": version,
        "database_revision": revision,
        "settings_schema": settings,
        "images": {
            "postgres": postgres,
            "redis": REDIS,
            "api": "ghcr.io/example/api@sha256:" + "d" * 64,
        },
    }


class UpdateSourceTests(unittest.TestCase):
    def test_sources_sharing_the_contract_pass(self):
        releases = {"1.4.5": published("1.4.5"), "1.4.6": published("1.4.6")}
        problems = check.incompatible_sources(TARGET, ["1.4.5", "1.4.6"], releases.__getitem__)
        self.assertEqual(problems, [])

    def test_a_newer_upstream_build_of_the_same_tag_is_reported(self):
        # 1.4.7 resolved postgres:16-alpine to a newer build, yet listed 1.4.3-1.4.6 as sources.
        target = {**TARGET, "images.postgres": NEWER_BUILD}
        releases = {"1.4.6": published("1.4.6")}
        problems = check.incompatible_sources(target, ["1.4.6"], releases.__getitem__)
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("1.4.6: images.postgres "), problems[0])

    def test_database_revision_and_settings_schema_must_match(self):
        releases = {"1.4.6": published("1.4.6", revision="0041_older", settings=2)}
        problems = check.incompatible_sources(TARGET, ["1.4.6"], releases.__getitem__)
        keys = sorted(problem.split(" ")[1] for problem in problems)
        self.assertEqual(keys, ["database_revision", "settings_schema"])

    def test_unpublished_mislabeled_and_malformed_sources_are_reported(self):
        def fetch(version):
            if version == "1.4.2":
                raise OSError("HTTP Error 404: Not Found")
            return published("1.4.5")

        problems = check.incompatible_sources(TARGET, ["1.4.2", "1.4.6", "1.4"], fetch)
        labels = [problem.split(":")[0] for problem in problems]
        self.assertEqual(labels, ["1.4.2", "1.4.6", "'1.4'"])

    def test_no_source_is_fetched_when_updates_are_disabled(self):
        problems = check.incompatible_sources(TARGET, [], lambda version: self.fail(version))
        self.assertEqual(problems, [])

    def test_repository_pins_storage_images_by_digest(self):
        services = production_compose(ROOT)["services"]
        contract = check.update_contract(ROOT)
        for name in check.STORAGE_SERVICES:
            self.assertIsNotNone(PINNED_IMAGE.fullmatch(services[name]["image"]), name)
            self.assertEqual(contract["images." + name], services[name]["image"])

    def test_resolved_images_replace_the_source_references(self):
        services = production_compose(ROOT)["services"]
        resolved = {
            services[name]["image"]: f"{name}@sha256:" + "e" * 64 for name in check.STORAGE_SERVICES
        }
        contract = check.update_contract(ROOT, resolved)
        self.assertEqual(contract["images.postgres"], "postgres@sha256:" + "e" * 64)

    def test_a_tag_without_digest_is_rejected(self):
        compose = {
            "services": {"postgres": {"image": "postgres:16-alpine"}, "redis": {"image": REDIS}}
        }
        with (
            mock.patch.object(check, "production_compose", return_value=compose),
            mock.patch.object(check, "database_head", return_value="0042_protection_halt"),
            self.assertRaisesRegex(ValueError, "postgres image must be pinned"),
        ):
            check.update_contract(ROOT)

    def test_command_fails_for_an_incompatible_source(self):
        target = check.update_contract(ROOT)
        matching = {
            "version": "1.4.6",
            "database_revision": target["database_revision"],
            "settings_schema": target["settings_schema"],
            "images": {name: target["images." + name] for name in check.STORAGE_SERVICES},
        }
        changed = {**matching, "images": {**matching["images"], "postgres": NEWER_BUILD}}
        arguments = ["--compatible-from", "1.4.6"]
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            self.assertEqual(check.main(arguments, lambda repository, version: matching), 0)
            self.assertEqual(check.main(arguments, lambda repository, version: changed), 1)
        self.assertIn("1.4.6: images.postgres", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
