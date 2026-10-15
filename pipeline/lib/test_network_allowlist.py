#!/usr/bin/env python3
"""The agent network allowlist: one list, used by Harbor and matched by the canary."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

import network_allowlist  # noqa: E402
from provenance import isolation_lines, isolation_view  # noqa: E402

CONFIG = LIB.parent / "config"


class NetworkAllowlistTest(unittest.TestCase):
    def test_hosts_are_the_deepseek_api(self) -> None:
        self.assertEqual(network_allowlist.hosts(), ["api.deepseek.com", "api.deepseek.ai"])

    def test_check_accepts_the_default_api_base_and_refuses_others(self) -> None:
        self.assertIsNone(network_allowlist.check("https://api.deepseek.com/v1"))
        problem = network_allowlist.check("https://api.openai.com/v1")
        self.assertIn("api.openai.com is not on the agent allowlist", problem or "")
        self.assertIn("cannot read a host", network_allowlist.check("not a url") or "")
        self.assertEqual(network_allowlist.main(["check", "--api-base", "https://evil.example/v1"]), 1)

    def test_canary_checks_the_same_hosts(self) -> None:
        canary = json.loads((CONFIG / "canary-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(canary["model_hosts"] + canary["model_host_aliases"]),
            sorted(network_allowlist.hosts()),
            "canary-v1.json model hosts drifted from network-allowlist-v1.json",
        )

    def test_record_lands_in_isolation_and_the_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp) / "eval_protocol_inputs.json"
            inputs.write_text(json.dumps({"isolation": {"mode": "git"}}), encoding="utf-8")
            doc = network_allowlist.record(inputs)
            got = json.loads(inputs.read_text(encoding="utf-8"))
        self.assertEqual(doc, got)
        self.assertEqual(got["isolation"]["mode"], "git")
        allow = got["isolation"]["network_allowlist"]
        self.assertEqual(allow["version"], "mac-k3d-network-allowlist-v1")
        self.assertEqual(allow["agent_hosts"], ["api.deepseek.com", "api.deepseek.ai"])
        self.assertEqual(len(allow["sha256"]), 64)
        lines = isolation_lines(isolation_view(got["isolation"]))
        self.assertIn(
            "Agent network: mac-k3d-network-allowlist-v1 · allowed api.deepseek.com, api.deepseek.ai",
            lines,
        )


if __name__ == "__main__":
    unittest.main()
