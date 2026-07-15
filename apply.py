#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["httpx"]
# ///

"""Apply the crates.io policies in this repository."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any

import httpx

CRATES_IO_API = "https://crates.io/api/v1"
USER_AGENT = "astral-sh-crates-policies (github.com/astral-sh/crates-policies)"
POLICIES_DIR = pathlib.Path(__file__).resolve().parent / "trusted-publishing"
CRATE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
PLACEHOLDER_VERSION = "0.0.0"
PUBLISH_DELAY_SECS = 15


@dataclass(frozen=True)
class Policy:
    path: pathlib.Path
    repository_owner: str
    repository_name: str
    workflow_filename: str
    environment: str | None
    trustpub_only: bool
    crates: tuple[str, ...]

    def matches(self, config: dict[str, Any]) -> bool:
        return (
            config.get("repository_owner") == self.repository_owner
            and config.get("repository_name") == self.repository_name
            and config.get("workflow_filename") == self.workflow_filename
            and config.get("environment") == self.environment
        )


@dataclass(frozen=True)
class Plan:
    crate: str
    policy: Policy
    publish_placeholder: bool
    configs_to_delete: tuple[int, ...]
    add_publisher: bool
    set_trustpub_only: bool

    @property
    def up_to_date(self) -> bool:
        return not (
            self.publish_placeholder
            or self.configs_to_delete
            or self.add_publisher
            or self.set_trustpub_only
        )


def load_policies(directory: pathlib.Path) -> list[Policy]:
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError(f"no policies found in {directory}")

    policies = []
    seen_crates: dict[str, pathlib.Path] = {}
    for path in paths:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path}: failed to load policy: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"{path}: policy must be a JSON object")

        required = {
            "repository_owner",
            "repository_name",
            "workflow_filename",
            "environment",
            "trustpub_only",
            "crates",
        }
        missing = required - data.keys()
        unknown = data.keys() - required
        if missing:
            raise ValueError(f"{path}: missing fields: {', '.join(sorted(missing))}")
        if unknown:
            raise ValueError(f"{path}: unknown fields: {', '.join(sorted(unknown))}")

        for field in ("repository_owner", "repository_name", "workflow_filename"):
            if not isinstance(data[field], str) or not data[field]:
                raise ValueError(f"{path}: `{field}` must be a non-empty string")
        if "/" in data["workflow_filename"]:
            raise ValueError(f"{path}: `workflow_filename` must be a filename")
        if data["environment"] is not None and (
            not isinstance(data["environment"], str) or not data["environment"]
        ):
            raise ValueError(
                f"{path}: `environment` must be a non-empty string or null"
            )
        if not isinstance(data["trustpub_only"], bool):
            raise ValueError(f"{path}: `trustpub_only` must be a boolean")
        if not isinstance(data["crates"], list) or not data["crates"]:
            raise ValueError(f"{path}: `crates` must be a non-empty list")

        crates = data["crates"]
        for crate in crates:
            if not isinstance(crate, str) or not CRATE_NAME.fullmatch(crate):
                raise ValueError(f"{path}: invalid crate name: {crate!r}")
        if crates != sorted(set(crates)):
            raise ValueError(f"{path}: `crates` must be sorted and unique")

        for crate in crates:
            if crate in seen_crates:
                raise ValueError(
                    f"{path}: `{crate}` is already configured in {seen_crates[crate]}"
                )
            seen_crates[crate] = path

        policies.append(
            Policy(
                path=path,
                repository_owner=data["repository_owner"],
                repository_name=data["repository_name"],
                workflow_filename=data["workflow_filename"],
                environment=data["environment"],
                trustpub_only=data["trustpub_only"],
                crates=tuple(crates),
            )
        )

    return policies


def get_crate_metadata(client: httpx.Client, crate: str) -> dict[str, Any] | None:
    response = client.get(f"{CRATES_IO_API}/crates/{crate}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json().get("crate")
    if not isinstance(payload, dict):
        raise ValueError(f"{crate}: unexpected crates.io metadata response")
    return payload


def list_trusted_publishers(client: httpx.Client, crate: str) -> list[dict[str, Any]]:
    response = client.get(
        f"{CRATES_IO_API}/trusted_publishing/github_configs",
        params={"crate": crate},
    )
    response.raise_for_status()
    configs = response.json().get("github_configs")
    if not isinstance(configs, list) or not all(
        isinstance(config, dict) for config in configs
    ):
        raise ValueError(f"{crate}: unexpected trusted publisher response")
    return configs


def plan_crate(
    policy: Policy,
    crate: str,
    metadata: dict[str, Any] | None,
    configs: list[dict[str, Any]],
) -> Plan:
    matching = [config for config in configs if policy.matches(config)]
    stale = [config for config in configs if not policy.matches(config)] + matching[1:]

    config_ids = []
    for config in stale:
        config_id = config.get("id")
        if not isinstance(config_id, int):
            raise ValueError(f"{crate}: trusted publisher is missing an integer `id`")
        config_ids.append(config_id)

    return Plan(
        crate=crate,
        policy=policy,
        publish_placeholder=metadata is None,
        configs_to_delete=tuple(config_ids),
        add_publisher=not matching,
        set_trustpub_only=bool(metadata and metadata.get("trustpub_only"))
        != policy.trustpub_only,
    )


def describe(plan: Plan, *, confirm: bool) -> str:
    if plan.up_to_date:
        return f"{plan.crate}: up-to-date"

    actions = []
    if plan.publish_placeholder:
        actions.append("publish placeholder")
    if plan.configs_to_delete:
        count = len(plan.configs_to_delete)
        noun = "publisher" if count == 1 else "publishers"
        actions.append(f"remove {count} {noun}")
    if plan.add_publisher:
        actions.append("add trusted publisher")
    if plan.set_trustpub_only:
        state = "enable" if plan.policy.trustpub_only else "disable"
        actions.append(f"{state} trustpub_only")

    prefix = "will" if confirm else "would"
    return f"{plan.crate}: {prefix} {' and '.join(actions)}"


def publish_placeholder(crate: str, policy: Policy) -> None:
    repository = (
        f"https://github.com/{policy.repository_owner}/{policy.repository_name}"
    )
    with tempfile.TemporaryDirectory(prefix=f"{crate}-placeholder-") as temp_dir:
        temp_path = pathlib.Path(temp_dir)
        src_dir = temp_path / "src"
        src_dir.mkdir()
        (temp_path / "Cargo.toml").write_text(
            "[package]\n"
            f"name = {json.dumps(crate)}\n"
            f"version = {json.dumps(PLACEHOLDER_VERSION)}\n"
            'edition = "2021"\n'
            'license = "MIT OR Apache-2.0"\n'
            f"repository = {json.dumps(repository)}\n"
            f"description = {json.dumps(f'Placeholder release for {crate}')}\n"
            'readme = "README.md"\n'
        )
        (temp_path / "README.md").write_text(
            f"# {crate}\n\n"
            f"This placeholder version ({PLACEHOLDER_VERSION}) reserves the crate name and "
            f"enables trusted publishing from [{policy.repository_owner}/"
            f"{policy.repository_name}]({repository}).\n"
        )
        (src_dir / "lib.rs").write_text(
            "//! Placeholder crate published to reserve the name on crates.io.\n"
        )
        result = subprocess.run(
            [
                "cargo",
                "publish",
                "--manifest-path",
                str(temp_path / "Cargo.toml"),
                "--no-verify",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"{crate}: placeholder publish failed\n{result.stderr}")


def apply_plan(client: httpx.Client, plan: Plan) -> None:
    if plan.publish_placeholder:
        publish_placeholder(plan.crate, plan.policy)

    for config_id in plan.configs_to_delete:
        response = client.delete(
            f"{CRATES_IO_API}/trusted_publishing/github_configs/{config_id}"
        )
        response.raise_for_status()

    if plan.add_publisher:
        response = client.post(
            f"{CRATES_IO_API}/trusted_publishing/github_configs",
            json={
                "github_config": {
                    "crate": plan.crate,
                    "repository_owner": plan.policy.repository_owner,
                    "repository_name": plan.policy.repository_name,
                    "workflow_filename": plan.policy.workflow_filename,
                    "environment": plan.policy.environment,
                }
            },
        )
        response.raise_for_status()

    if plan.set_trustpub_only:
        response = client.patch(
            f"{CRATES_IO_API}/crates/{plan.crate}",
            json={"crate": {"trustpub_only": plan.policy.trustpub_only}},
        )
        response.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Apply the policies; the default is a read-only dry run",
    )
    args = parser.parse_args()

    try:
        policies = load_policies(POLICIES_DIR)
        token = os.environ.get("CARGO_REGISTRY_TOKEN", "")
        if not token:
            raise ValueError(
                "CARGO_REGISTRY_TOKEN is required with the `publish-new` and "
                "`trusted-publishing` scopes"
            )

        headers = {"User-Agent": USER_AGENT}
        auth_headers = {**headers, "Authorization": token}
        with (
            httpx.Client(headers=headers, timeout=30) as public_client,
            httpx.Client(headers=auth_headers, timeout=30) as auth_client,
        ):
            plans = []
            for policy in policies:
                for crate in policy.crates:
                    metadata = get_crate_metadata(public_client, crate)
                    configs = (
                        list_trusted_publishers(auth_client, crate)
                        if metadata is not None
                        else []
                    )
                    plans.append(plan_crate(policy, crate, metadata, configs))

            for plan in plans:
                print(describe(plan, confirm=args.confirm))

            if args.confirm:
                published_any = False
                for plan in plans:
                    if plan.publish_placeholder and published_any:
                        time.sleep(PUBLISH_DELAY_SECS)
                    apply_plan(auth_client, plan)
                    published_any = published_any or plan.publish_placeholder

    except (ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as exc:
        print(f"error {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        if exc.response.status_code in {401, 403}:
            print(
                "hint: check that the token has the `publish-new` and "
                "`trusted-publishing` scopes and "
                "that its owner owns every configured crate",
                file=sys.stderr,
            )
        return 1
    except httpx.HTTPError as exc:
        print(f"error communicating with crates.io: {exc}", file=sys.stderr)
        return 1

    changed = sum(not plan.up_to_date for plan in plans)
    action = "applied" if args.confirm else "would change"
    print(f"{action}: {changed}/{len(plans)} crates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
