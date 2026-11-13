"""Contract checks for the manually maintained Agent entry documents."""
import contextlib
import io
from pathlib import Path
import re
import unittest
from localization_toolkit.cli import parser

ROOT = Path(__file__).resolve().parents[1]
DOCS = ['AGENTS.md', 'README.md', 'docs/quickstart-for-ai-users.md',
        'docs/ai-readiness-audit.md', 'docs/agent-entry-validation.md',
        'skills/game-localization/SKILL.md',
        'skills/game-localization/references/analysis.md',
        'skills/game-localization/references/release.md']

def local_links(document):
    text = document.read_text(encoding='utf-8')
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
        if '://' not in target and not target.startswith('#'):
            yield document.parent / target.split('#', 1)[0]

class AgentDocsTests(unittest.TestCase):
    def test_entry_files_exist(self):
        for name in DOCS:
            with self.subTest(name=name):
                self.assertTrue((ROOT / name).is_file())

    def test_skill_frontmatter_is_supported_yaml_scalar_mapping(self):
        text = (ROOT / DOCS[5]).read_text(encoding='utf-8')
        self.assertTrue(text.startswith('---\n'))
        header = text.split('---', 2)[1]
        values = {}
        for line in header.strip().splitlines():
            match = re.fullmatch(r'([a-z]+): ([A-Za-z][A-Za-z0-9 ,./()_-]*)', line)
            self.assertIsNotNone(match, 'Use simple unambiguous YAML scalars')
            key, value = match.groups()
            self.assertNotIn(key, values)
            values[key] = value
        self.assertEqual(values['name'], 'game-localization')
        self.assertGreater(len(values['description']), 40)

    def test_local_document_links_resolve(self):
        for name in DOCS:
            for path in local_links(ROOT / name):
                with self.subTest(document=name, path=str(path)):
                    self.assertTrue(path.resolve().exists())
                    self.assertTrue(path.resolve().is_relative_to(ROOT))

    def test_documented_cli_commands_are_real(self):
        found = set()
        for name in ('README.md', 'docs/quickstart-for-ai-users.md'):
            text = (ROOT / name).read_text(encoding='utf-8')
            found.update(re.findall(r'localize(?:\.exe)?\s+([a-z]+)', text))
        self.assertTrue({'init', 'inspect', 'extract', 'status'} <= found)
        for command in found:
            with self.subTest(command=command), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    parser().parse_args([command, '--help'])
                self.assertEqual(result.exception.code, 0)

    def test_agent_safety_contract(self):
        text = (ROOT / 'AGENTS.md').read_text(encoding='utf-8')
        for phrase in ('Never assume a game uses BGI', 'read-only analysis first',
                       'Original game directories must remain read-only',
                       'Human review cannot be skipped', 'Mock translation is not real translation',
                       'Static validation is not in-game acceptance', 'DRM', 'anti-cheat',
                       'unknown hashes', 'actual validation evidence', 'unauthorized third-party'):
            self.assertIn(phrase, text)

    def test_quickstart_preserves_limitations(self):
        text = (ROOT / 'docs/quickstart-for-ai-users.md').read_text(encoding='utf-8')
        for phrase in ('not one-click', 'Mock translation is not real translation',
                       'Human review cannot be skipped', 'Static validation is not in-game acceptance',
                       'legally held', 'not engine detection', 'automatic discovery across hosts has not been tested'):
            self.assertIn(phrase, text)
        self.assertIn('-m venv', text)
        self.assertIn('launcher is optional', text)

    def test_examples_are_real_and_all_seven_listed(self):
        text = (ROOT / 'docs/quickstart-for-ai-users.md').read_text(encoding='utf-8')
        examples = re.findall(r'examples/[A-Za-z_/]+\.py', text)
        self.assertEqual(len(set(examples)), 7)
        for example in examples:
            self.assertTrue((ROOT / example).is_file())

    def test_no_runtime_source_changes_are_required_by_entry_docs(self):
        text = (ROOT / 'docs/ai-readiness-audit.md').read_text(encoding='utf-8')
        self.assertIn('Batch B proposals — not implemented', text)
        self.assertIn('No core runtime behavior is changed', text)
        self.assertIn('Exit 0 does not imply', text)