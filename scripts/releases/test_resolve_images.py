from __future__ import annotations

import unittest

import resolve_images


class ImageResolutionTests(unittest.TestCase):
    def inspect(self, reference):
        return {
            "digest": "sha256:" + "a" * 64,
            "manifests": [
                {"platform": {"os": "linux", "architecture": "amd64"}},
                {"platform": {"os": "linux", "architecture": "arm64", "variant": "v8"}},
                {"platform": {"os": "unknown", "architecture": "unknown"}},
            ],
        }

    def test_upstream_and_built_images_resolve_to_both_platforms(self):
        built_pin = "ghcr.io/example/api@sha256:" + "a" * 64
        result = resolve_images.resolve_images(
            ["alpine:3.20", "api:local"], {"api:local": built_pin}, self.inspect
        )
        self.assertEqual(result["api:local"], built_pin)
        self.assertEqual(result["alpine:3.20"], "alpine:3.20@sha256:" + "a" * 64)

    def test_missing_platform_is_rejected(self):
        def one_platform(reference):
            manifest = self.inspect(reference)
            manifest["manifests"] = manifest["manifests"][:1]
            return manifest

        with self.assertRaisesRegex(ValueError, "platform"):
            resolve_images.resolve_images(["api:local"], {}, one_platform)

    def test_built_digest_cannot_be_replaced(self):
        with self.assertRaisesRegex(ValueError, "changed"):
            resolve_images.resolve_images(
                ["api:local"],
                {"api:local": "ghcr.io/example/api@sha256:" + "b" * 64},
                self.inspect,
            )


if __name__ == "__main__":
    unittest.main()
