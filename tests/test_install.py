import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("installer", Path(__file__).resolve().parents[1] / "scripts/install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "repo"
        self.project.mkdir()

    def test_preview_does_not_mutate_and_all_runners_install(self):
        plan = installer.build_plan(self.project, list(installer.RUNNERS))
        self.assertEqual(list(self.project.iterdir()), [])
        installer.apply_plan(self.project, plan)
        for folder in (
            ".agents/skills/context-router",
            ".claude/skills/context-router",
            ".opencode/skills/context-router",
        ):
            self.assertTrue((self.project / folder / "scripts/context_router.py").is_file())
        self.assertTrue((self.project / ".opencode/commands/context-route.md").is_file())
        self.assertTrue((self.project / ".claude/commands/context-route.md").is_file())
        self.assertEqual(list(self.project.rglob("*.pyc")), [])
        self.assertEqual(installer.build_plan(self.project, list(installer.RUNNERS)), [])

    def test_command_mentions_local_flow_and_explicit_remote_gate(self):
        installer.apply_plan(self.project, installer.build_plan(self.project, ["opencode"]))
        command = (self.project / ".opencode/commands/context-route.md").read_text()
        self.assertIn("task-search", command)
        self.assertIn("--jev", command)
        self.assertIn("--allow-remote", command)

        other = self.root / "codex-repo"
        other.mkdir()
        installer.apply_plan(other, installer.build_plan(other, ["codex"]))
        self.assertFalse((other / ".opencode").exists())
        self.assertFalse((other / ".claude").exists())

    def test_existing_bootstrap_text_preserved(self):
        original = "# Team rules\nDo not commit indexes.\n"
        (self.project / "AGENTS.md").write_text(original)
        installer.apply_plan(self.project, installer.build_plan(self.project, ["codex"]))
        self.assertTrue((self.project / "AGENTS.md").read_text().startswith(original))

    def test_collision_prevents_partial_install(self):
        existing = self.project / ".claude/skills/context-router/SKILL.md"
        existing.parent.mkdir(parents=True)
        existing.write_text("custom skill")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            installer.build_plan(self.project, list(installer.RUNNERS))
        self.assertFalse((self.project / ".agents").exists())
        self.assertEqual(existing.read_text(), "custom skill")

    def test_symlink_escape_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.project / ".agents").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            installer.build_plan(self.project, ["codex"])
        self.assertEqual(list(outside.iterdir()), [])

    def test_change_after_preview_is_rejected_before_writes(self):
        plan = installer.build_plan(self.project, ["codex"])
        (self.project / "AGENTS.md").write_text("concurrent edit")
        with self.assertRaisesRegex(ValueError, "changed"):
            installer.apply_plan(self.project, plan)
        self.assertFalse((self.project / ".agents").exists())

    def test_add_runners_in_either_order_preserves_bootstrap(self):
        (self.project / "AGENTS.md").write_text("Original rules\n")
        for runner in ("opencode", "codex", "opencode"):
            installer.apply_plan(self.project, installer.build_plan(self.project, [runner]))
        content = (self.project / "AGENTS.md").read_text()
        self.assertTrue(content.startswith("Original rules\n"))
        self.assertEqual(content.count(installer.BEGIN), 1)
        self.assertIn(".agents/skills/context-router", content)

    def test_custom_managed_block_refused(self):
        (self.project / "AGENTS.md").write_text(installer.BEGIN + "\ncustom\n" + installer.END)
        with self.assertRaisesRegex(ValueError, "merge manually"):
            installer.build_plan(self.project, ["codex"])

    def test_claude_bootstrap_targets_claude_memory(self):
        installer.apply_plan(self.project, installer.build_plan(self.project, ["claude"]))
        self.assertTrue((self.project / "CLAUDE.md").is_file())
        self.assertFalse((self.project / "AGENTS.md").exists())
        self.assertIn(installer.BEGIN, (self.project / "CLAUDE.md").read_text())

    def test_no_bootstrap_option(self):
        installer.apply_plan(self.project, installer.build_plan(self.project, ["claude"], False))
        self.assertFalse((self.project / "CLAUDE.md").exists())
        self.assertTrue((self.project / ".claude/skills/context-router/SKILL.md").is_file())

    def test_source_skill_symlink_is_rejected(self):
        bundle = self.root / "bundle"
        bundle.mkdir()
        outside = self.root / "outside.md"
        outside.write_text("outside")
        (bundle / "linked.md").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            installer.skill_files(bundle)


if __name__ == "__main__":
    unittest.main()
