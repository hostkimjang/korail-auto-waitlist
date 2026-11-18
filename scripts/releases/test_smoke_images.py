from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import smoke_images


class SmokeCleanupTests(unittest.TestCase):
    def test_failed_runtime_is_removed_by_captured_container_id(self):
        responses = [
            SimpleNamespace(stdout="task-container-id\n"),
            subprocess.CalledProcessError(1, ["docker", "start"]),
            SimpleNamespace(returncode=0),
        ]
        with patch("smoke_images.subprocess.run", side_effect=responses) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                smoke_images.run_image(
                    "registry.example/image@sha256:" + "a" * 64,
                    "linux/amd64",
                    "python",
                    ["--version"],
                )
        self.assertEqual(
            run.call_args_list[-1].args[0], ["docker", "rm", "--force", "task-container-id"]
        )

    def test_success_requires_container_exit_zero_and_cleanup(self):
        responses = [
            SimpleNamespace(stdout="task-container-id\n"),
            SimpleNamespace(returncode=0),
            SimpleNamespace(stdout="1\n"),
            SimpleNamespace(returncode=0),
        ]
        with patch("smoke_images.subprocess.run", side_effect=responses) as run:
            with self.assertRaisesRegex(ValueError, "smoke failed"):
                smoke_images.run_image(
                    "registry.example/image@sha256:" + "a" * 64,
                    "linux/arm64",
                    "python",
                    ["--version"],
                )
        self.assertEqual(
            run.call_args_list[-1].args[0], ["docker", "rm", "--force", "task-container-id"]
        )


if __name__ == "__main__":
    unittest.main()
