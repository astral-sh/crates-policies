#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
#
# [tool.uv]
# exclude-newer = "P7D"
# no-build = true
# ///

"""Check that a Cargo workspace's publishable crates are configured and seeded."""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request

CRATES_IO_INDEX = "https://index.crates.io"
USER_AGENT = "astral-sh-crates-policies (github.com/astral-sh/crates-policies)"
POLICIES_DIR = pathlib.Path(__file__).resolve().parent / "trusted-publishing"


def repository_from_manifest(manifest_path: pathlib.Path) -> str:
    manifest = tomllib.loads(manifest_path.read_text())
    repository = manifest.get("workspace", {}).get("package", {}).get("repository")
    if repository is None:
        repository = manifest.get("package", {}).get("repository")
    if not isinstance(repository, str):
        raise RuntimeError("package repository is missing from Cargo.toml")

    parsed = urllib.parse.urlparse(repository)
    if parsed.netloc != "github.com":
        raise RuntimeError(
            f"workspace repository is not hosted on GitHub: {repository}"
        )
    parts = parsed.path.removesuffix(".git").strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        raise RuntimeError(f"invalid workspace repository: {repository}")
    return "/".join(parts)


def configured_crates(repository: str) -> set[str]:
    owner, name = repository.split("/", 1)
    configured = set()
    matched = False

    for path in sorted(POLICIES_DIR.glob("*.json")):
        policy = json.loads(path.read_text())
        if (
            policy.get("repository_owner") != owner
            or policy.get("repository_name") != name
        ):
            continue
        matched = True
        crates = policy.get("crates")
        if not isinstance(crates, list) or not all(
            isinstance(crate, str) for crate in crates
        ):
            raise RuntimeError(f"`{path.name}` does not contain a crate list")
        configured.update(crates)

    if not matched:
        raise RuntimeError(f"no trusted-publishing policy found for {repository}")
    return configured


def publishable_crates(manifest_path: pathlib.Path) -> set[str]:
    result = subprocess.run(
        [
            "cargo",
            "metadata",
            "--format-version",
            "1",
            "--no-deps",
            "--manifest-path",
            str(manifest_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    metadata = json.loads(result.stdout)
    members = set(metadata["workspace_members"])
    return {
        package["name"]
        for package in metadata["packages"]
        if package["id"] in members and package.get("publish") != []
    }


def crate_exists(crate: str) -> bool:
    # Cargo's registry index stores crate metadata at name-derived paths:
    # https://doc.rust-lang.org/cargo/reference/registry-index.html#index-files
    name = crate.lower()
    if len(name) <= 2:
        prefix = str(len(name))
    elif len(name) == 3:
        prefix = f"3/{name[0]}"
    else:
        prefix = f"{name[:2]}/{name[2:4]}"

    request = urllib.request.Request(
        f"{CRATES_IO_INDEX}/{prefix}/{name}",
        headers={"User-Agent": USER_AGENT},
        method="HEAD",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError(
                    f"{crate}: crates.io lookup failed: HTTP {response.status}"
                )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise RuntimeError(
            f"{crate}: crates.io lookup failed: HTTP {exc.code}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"{crate}: crates.io lookup failed: {exc}") from exc
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workspace", type=pathlib.Path, help="Path to a Cargo workspace"
    )
    parser.add_argument(
        "--repository",
        help="GitHub repository to check (OWNER/NAME); inferred from Cargo.toml by default",
    )
    args = parser.parse_args()

    manifest_path = args.workspace.resolve() / "Cargo.toml"
    if not manifest_path.is_file():
        print(f"error: no Cargo.toml found in {args.workspace}", file=sys.stderr)
        return 1

    try:
        repository = args.repository or repository_from_manifest(manifest_path)
        if repository.count("/") != 1 or not all(repository.split("/")):
            raise RuntimeError(f"invalid GitHub repository: {repository}")
        publishable = publishable_crates(manifest_path)
        configured = configured_crates(repository)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    new_crates = sorted(publishable - configured)
    if new_crates:
        print(
            f"Crates requiring crates.io publish setup: {', '.join(new_crates)}",
            file=sys.stderr,
        )
        print(
            "Register the new crates in astral-sh/crates-policies.",
            file=sys.stderr,
        )
        return 1

    try:
        unseeded = [crate for crate in sorted(publishable) if not crate_exists(crate)]
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if unseeded:
        print(
            f"Crates missing from crates.io: {', '.join(unseeded)}",
            file=sys.stderr,
        )
        print(
            "Run the Apply workflow in astral-sh/crates-policies with confirm enabled.",
            file=sys.stderr,
        )
        return 1

    print(
        f"All {len(publishable)} publishable crates in {repository} are configured "
        "and exist on crates.io."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
