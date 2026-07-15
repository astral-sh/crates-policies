#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///

"""Check that a Cargo workspace's publishable crates are configured."""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tomllib
import urllib.parse

POLICIES_DIR = pathlib.Path(__file__).resolve().parent / "trusted-publishing"


def repository_from_manifest(manifest_path: pathlib.Path) -> str:
    manifest = tomllib.loads(manifest_path.read_text())
    repository = manifest.get("workspace", {}).get("package", {}).get("repository")
    if not isinstance(repository, str):
        raise RuntimeError("`workspace.package.repository` is missing from Cargo.toml")

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
            "Bootstrap the new crates, then add them to astral-sh/crates-policies.",
            file=sys.stderr,
        )
        return 1

    print(f"All {len(publishable)} publishable crates in {repository} are configured.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
