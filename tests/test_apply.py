from __future__ import annotations

import json
import pathlib
import unittest
from unittest.mock import patch

import httpx

import apply


def policy(**changes: object) -> apply.Policy:
    values = {
        "path": pathlib.Path("trusted-publishing/example.json"),
        "repository_owner": "astral-sh",
        "repository_name": "example",
        "workflow_filename": "release.yml",
        "environment": "release",
        "trustpub_only": True,
        "crates": ("example",),
    }
    values.update(changes)
    return apply.Policy(**values)


def config(**changes: object) -> dict[str, object]:
    values = {
        "id": 1,
        "crate": "example",
        "repository_owner": "astral-sh",
        "repository_name": "example",
        "workflow_filename": "release.yml",
        "environment": "release",
    }
    values.update(changes)
    return values


class PolicyTests(unittest.TestCase):
    def test_uv_policy(self) -> None:
        policies = apply.load_policies(apply.POLICIES_DIR)

        self.assertEqual(len(policies), 1)
        uv = policies[0]
        self.assertEqual(uv.repository_owner, "astral-sh")
        self.assertEqual(uv.repository_name, "uv")
        self.assertEqual(uv.workflow_filename, "release.yml")
        self.assertEqual(uv.environment, "release")
        self.assertTrue(uv.trustpub_only)
        self.assertEqual(len(uv.crates), 68)
        self.assertEqual(uv.crates, tuple(sorted(set(uv.crates))))

    def test_rejects_duplicate_crate_across_policies(self) -> None:
        first = pathlib.Path("trusted-publishing/first.json")
        second = pathlib.Path("trusted-publishing/second.json")
        data = json.dumps(
            {
                "repository_owner": "astral-sh",
                "repository_name": "example",
                "workflow_filename": "release.yml",
                "environment": "release",
                "trustpub_only": True,
                "crates": ["example"],
            }
        )

        with (
            patch.object(pathlib.Path, "glob", return_value=[first, second]),
            patch.object(pathlib.Path, "read_text", return_value=data),
            self.assertRaisesRegex(ValueError, "already configured"),
        ):
            apply.load_policies(pathlib.Path("trusted-publishing"))

    def test_rejects_unsorted_crates(self) -> None:
        path = pathlib.Path("trusted-publishing/example.json")
        data = json.dumps(
            {
                "repository_owner": "astral-sh",
                "repository_name": "example",
                "workflow_filename": "release.yml",
                "environment": "release",
                "trustpub_only": True,
                "crates": ["z", "a"],
            }
        )

        with (
            patch.object(pathlib.Path, "glob", return_value=[path]),
            patch.object(pathlib.Path, "read_text", return_value=data),
            self.assertRaisesRegex(ValueError, "sorted and unique"),
        ):
            apply.load_policies(pathlib.Path("trusted-publishing"))


class PlanTests(unittest.TestCase):
    def test_up_to_date(self) -> None:
        plan = apply.plan_crate(
            policy(), "example", {"trustpub_only": True}, [config()]
        )

        self.assertTrue(plan.up_to_date)
        self.assertEqual(apply.describe(plan, confirm=False), "example: up-to-date")

    def test_removes_stale_and_duplicate_publishers(self) -> None:
        plan = apply.plan_crate(
            policy(),
            "example",
            {"trustpub_only": False},
            [config(id=1), config(id=2), config(id=3, environment=None)],
        )

        self.assertEqual(plan.configs_to_delete, (3, 2))
        self.assertFalse(plan.add_publisher)
        self.assertTrue(plan.set_trustpub_only)
        self.assertEqual(
            apply.describe(plan, confirm=False),
            "example: would remove 2 publishers and enable trustpub_only",
        )

    def test_adds_missing_publisher(self) -> None:
        plan = apply.plan_crate(policy(), "example", {"trustpub_only": True}, [])

        self.assertTrue(plan.add_publisher)
        self.assertEqual(
            apply.describe(plan, confirm=True),
            "example: will add trusted publisher",
        )


class ApiTests(unittest.TestCase):
    def test_reads_metadata_and_publishers(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/crates/example":
                return httpx.Response(200, json={"crate": {"trustpub_only": True}})
            self.assertEqual(
                request.url.path, "/api/v1/trusted_publishing/github_configs"
            )
            self.assertEqual(request.url.params["crate"], "example")
            return httpx.Response(200, json={"github_configs": [config()]})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            metadata = apply.get_crate_metadata(client, "example")
            configs = apply.list_trusted_publishers(client, "example")

        self.assertTrue(metadata["trustpub_only"])
        self.assertEqual(configs, [config()])

    def test_missing_crate_has_actionable_error(self) -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(404))
        with (
            httpx.Client(transport=transport) as client,
            self.assertRaisesRegex(ValueError, "requires an initial publish"),
        ):
            apply.get_crate_metadata(client, "example")

    def test_applies_delete_create_and_trustpub_only(self) -> None:
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200)

        plan = apply.Plan(
            crate="example",
            policy=policy(),
            configs_to_delete=(7,),
            add_publisher=True,
            set_trustpub_only=True,
        )
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            apply.apply_plan(client, plan)

        self.assertEqual(
            [(request.method, request.url.path) for request in requests],
            [
                ("DELETE", "/api/v1/trusted_publishing/github_configs/7"),
                ("POST", "/api/v1/trusted_publishing/github_configs"),
                ("PATCH", "/api/v1/crates/example"),
            ],
        )
        self.assertEqual(
            json.loads(requests[1].content),
            {
                "github_config": {
                    "crate": "example",
                    "repository_owner": "astral-sh",
                    "repository_name": "example",
                    "workflow_filename": "release.yml",
                    "environment": "release",
                }
            },
        )
        self.assertEqual(
            json.loads(requests[2].content),
            {"crate": {"trustpub_only": True}},
        )


if __name__ == "__main__":
    unittest.main()
