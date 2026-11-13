import json
import re
import unittest
from pathlib import Path

from localization_toolkit.cli import parser
from localization_toolkit.game_analysis import GameAnalysisReportError, validate_game_analysis_report

ROOT = Path(__file__).parents[1]


class GameAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.report = json.loads((ROOT / "examples/game-analysis/reports/game-analysis.json").read_text(encoding="utf-8"))

    def test_synthetic_report_schema_is_valid(self):
        validate_game_analysis_report(self.report)

    def test_required_fields_are_enforced(self):
        report = dict(self.report); report.pop("next_actions")
        with self.assertRaises(GameAnalysisReportError): validate_game_analysis_report(report)

    def test_evidence_level_enum_is_enforced(self):
        report = json.loads(json.dumps(self.report)); report["evidence"][0]["level"] = "guessed"
        with self.assertRaises(GameAnalysisReportError): validate_game_analysis_report(report)

    def test_unknown_engine_cannot_be_confirmed(self):
        report = json.loads(json.dumps(self.report)); report["engine"] = {"name": "unknown", "evidence_level": "confirmed"}
        with self.assertRaises(GameAnalysisReportError): validate_game_analysis_report(report)

    def test_stage_nine_needs_runtime_evidence(self):
        report = json.loads(json.dumps(self.report)); report["stages"]["stage_9"] = "passed"
        with self.assertRaises(GameAnalysisReportError): validate_game_analysis_report(report)

    def test_stage_nine_requires_runtime_unverified_without_evidence(self):
        report = json.loads(json.dumps(self.report)); report["stages"]["stage_9"] = "not_started"
        with self.assertRaises(GameAnalysisReportError): validate_game_analysis_report(report)

    def test_completed_runtime_stage_requires_observation_record(self):
        report = json.loads(json.dumps(self.report)); report["stages"]["stage_9"] = "passed"
        report["runtime_evidence"] = [{"type": "manual play", "source": "synthetic operator log", "observed_at": "2026-01-01T00:00:00Z"}]
        validate_game_analysis_report(report)

    def test_resource_candidate_requires_evidence_level(self):
        report = json.loads(json.dumps(self.report)); report["resource_candidates"][0].pop("evidence_level")
        with self.assertRaises(GameAnalysisReportError): validate_game_analysis_report(report)

    def test_fixture_remains_unknown_and_unverified(self):
        self.assertEqual(self.report["engine"]["evidence_level"], "unknown")
        self.assertEqual(self.report["stages"]["stage_9"], "runtime_unverified")

    def test_agent_entrypoints_link_to_protocol_docs(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        skill = (ROOT / "skills/game-localization/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("docs/game-analysis-workflow.md", agents)
        self.assertIn("docs/ai-task-contract.md", agents)
        self.assertIn("game-analysis-workflow.md", skill)
        self.assertIn("ai-task-contract.md", skill)

    def test_local_markdown_references_exist(self):
        for rel in ("AGENTS.md", "skills/game-localization/SKILL.md", "docs/game-analysis-workflow.md",
                    "docs/ai-task-contract.md", "examples/game-analysis/README.md"):
            path = ROOT / rel
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)]+)\)", text):
                if "://" in target or target.startswith("#"):
                    continue
                self.assertTrue((path.parent / target.split("#", 1)[0]).exists(), f"{rel}: {target}")

    def test_example_json_is_parseable_and_example_command_exists(self):
        sample = json.loads((ROOT / "examples/game-analysis/reports/game-analysis.json").read_text(encoding="utf-8"))
        validate_game_analysis_report(sample)
        commands = parser()._subparsers._group_actions[0].choices
        self.assertIn("inspect", commands)
        self.assertIn("translate-import", commands)

    def test_no_engine_compatibility_claim_for_unknown_probe(self):
        self.assertEqual(self.report["engine"]["name"], "unknown")
        self.assertTrue(any("not confirmed" in item.lower() or "unknown" in item.lower()
                            for item in self.report["unknowns"]))

    def test_every_stage_has_allowed_status(self):
        validate_game_analysis_report(self.report)
        self.assertEqual(len(self.report["stages"]), 11)


if __name__ == "__main__": unittest.main()
