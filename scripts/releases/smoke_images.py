"""Smoke test the exact published digests on each native Linux architecture."""

from __future__ import annotations

import argparse
import json
import subprocess
import uuid
from pathlib import Path

from build_bundle import PINNED_IMAGE, PLATFORMS


def run_image(image: str, platform: str, entrypoint: str, arguments: list[str], tmpfs: tuple[str, ...] = ()) -> None:
    identifier = None
    try:
        created = subprocess.run(
            [
                "docker",
                "create",
                "--name",
                "railwait-release-smoke-" + uuid.uuid4().hex,
                "--network",
                "none",
                "--platform",
                platform,
                *[item for mount in tmpfs for item in ("--tmpfs", mount)],
                "--entrypoint",
                entrypoint,
                image,
                *arguments,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        identifier = created.stdout.strip()
        subprocess.run(["docker", "start", "--attach", identifier], check=True, timeout=90)
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.ExitCode}}", identifier],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if inspected.stdout.strip() != "0":
            raise ValueError("runtime image smoke failed")
    finally:
        if identifier:
            subprocess.run(["docker", "rm", "--force", identifier], check=True, timeout=30)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--platform", choices=PLATFORMS, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    checks = {
        "api": (
            "python",
            ["-c", "import rail_waitlist.main; import rail_waitlist.admin_auth.bootstrap"],
        ),
        "web": ("sh", ["-ec", "nginx -t && test -s /usr/share/nginx/html/index.html"]),
        "korail-browser-adapter": (
            "python",
            [
                "-c",
                "import pydoll; import subprocess; "
                "subprocess.run(['/usr/bin/google-chrome', '--version'], check=True, timeout=15)",
            ],
        ),
        "backup": (
            "sh",
            [
                "-ec",
                "command -v age; command -v pg_dump; "
                "test -x /scripts/backup-postgres.sh; test -x /scripts/restore-postgres.sh; "
                "test -x /scripts/update-backup.sh",
            ],
        ),
    }
    for service, (entrypoint, arguments) in checks.items():
        image = manifest["images"][service]
        if not PINNED_IMAGE.fullmatch(image):
            raise ValueError("smoke image must use a digest")
        tmpfs = ("/var/run:uid=101,gid=101,mode=0755", "/var/cache/nginx:uid=101,gid=101,mode=0755") if service == "web" else ()
        run_image(image, args.platform, entrypoint, arguments, tmpfs)


if __name__ == "__main__":
    main()
