import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import context_router as router
import shadow
import parser as cli


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        return self.body


class ContextRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "game"
        (self.project / "know-how").mkdir(parents=True)
        (self.project / "survivor/shop").mkdir(parents=True)
        (self.project / "know-how/tasks/2026-09-01-shop-cancellation").mkdir(parents=True)
        (self.project / "project.godot").write_text("config_version=5\n")
        (self.project / "know-how/ARCHITECTURE.md").write_text(
            "# Economy\n\nShop cancellation must preserve the remaining service use.\n"
            "\n## Rendering\n\nThe renderer uses compatibility mode.\n"
        )
        (self.project / "survivor/shop/service.gd").write_text(
            "extends Node\n\nfunc cancel_service():\n    remaining_uses += 1\n"
        )
        (self.project / "know-how/tasks/2026-09-01-shop-cancellation/plan.md").write_text(
            "# Shop cancellation\n\nPreserve remaining service uses and verify the cancel path.\n"
        )

    def test_load_env_file_parses_ignoring_comments_and_blank_lines(self):
        env_file = self.project / ".env"
        env_file.write_text("# comment\n\nTYPESAFE_API_KEY=abc123\nCONTEXT_ROUTER_JEV=0\nQUOTED=\"x y\"\n")
        values = router.load_env_file(env_file)
        self.assertEqual(values, {"TYPESAFE_API_KEY": "abc123", "CONTEXT_ROUTER_JEV": "0", "QUOTED": "x y"})

    def test_load_env_file_rejects_malformed_lines(self):
        env_file = self.project / ".env"
        env_file.write_text("GOOD=1\nNOT_A_PAIR\n")
        with self.assertRaisesRegex(ValueError, "Malformed"):
            router.load_env_file(env_file)

    def test_jev_kill_switch_blocks_shadow_even_with_key_and_consent(self):
        records = router.build_index(self.project, chunk_lines=10)
        opener = mock.Mock()
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "secret", "CONTEXT_ROUTER_JEV": "0"}), mock.patch.object(
            shadow.urllib.request, "build_opener", return_value=opener
        ):
            with self.assertRaisesRegex(RuntimeError, "CONTEXT_ROUTER_JEV=0"):
                shadow.shadow(records, "shop cancellation", "tdd", self.project / "cache.jsonl", limit=1)
        opener.open.assert_not_called()

    def test_parser_rejects_invalid_jev_toggle(self):
        with mock.patch.dict(os.environ, {"CONTEXT_ROUTER_JEV": "maybe"}):
            self.assertEqual(
                cli.main(["search", "--index", str(self.project / "i.jsonl"), "--query", "x"]), 2
            )

    def test_start_scaffolds_task_partition_without_overwriting(self):
        report = router.start_partition(self.project, "2026-09-23-shop-fix")
        created = {Path(p).as_posix() for p in report["created"]}
        self.assertIn("tasks/2026-09-23-shop-fix/spec.md", created)
        self.assertIn("tasks/2026-09-23-shop-fix/plan.md", created)
        self.assertIn("tasks/2026-09-23-shop-fix/evidence.md", created)
        self.assertIn("architecture/README.md", created)
        self.assertTrue(report["guidance"])
        self.assertEqual(report["skipped"], [])
        second = router.start_partition(self.project, "2026-09-23-shop-fix")
        self.assertEqual(set(second["created"]), set())
        self.assertEqual(len(second["skipped"]), 4)

    def test_start_rejects_unsafe_task_id(self):
        for bad in ("../escape", "a/b", "", "."):
            with self.assertRaises(ValueError):
                router.start_partition(self.project, bad)

    def test_start_scans_existing_prior_task_records(self):
        (self.project / "specs").mkdir()
        (self.project / "specs/old-thing-design.md").write_text("# Old\n")
        (self.project / "docs/superpowers/plans").mkdir(parents=True)
        (self.project / "docs/superpowers/plans/2026-08-01-x.md").write_text("# P\n")
        report = router.start_partition(self.project, "new-task")
        self.assertIn("specs/old-thing-design.md", report["existing_prior_tasks"])
        self.assertIn("docs/superpowers/plans/2026-08-01-x.md", report["existing_prior_tasks"])
        self.assertIn("know-how/tasks/2026-09-01-shop-cancellation", report["existing_prior_tasks"])

    def test_start_with_index_builds_loadable_index(self):
        out = self.project / "private-index.jsonl"
        report = router.start_partition(self.project, "idx-task", index_out=out)
        self.assertEqual(report["index"]["chunks"], len(router.read_jsonl(out)))

    def test_kind_mapping_is_unambiguous(self):
        kinds = {
            "data.json": router._kind(Path("data.json")),
            "data.jsonc": router._kind(Path("data.jsonc")),
            "conf.yaml": router._kind(Path("conf.yaml")),
            "scene.tscn": router._kind(Path("scene.tscn")),
            "main.gd": router._kind(Path("main.gd")),
            "notes.md": router._kind(Path("notes.md")),
            "package.json": router._kind(Path("package.json")),
        }
        self.assertEqual(kinds, {
            "data.json": "data",
            "data.jsonc": "data",
            "conf.yaml": "configuration",
            "scene.tscn": "configuration",
            "main.gd": "code",
            "notes.md": "markdown",
            "package.json": "configuration",
        })

    def test_index_is_stable_and_search_explains_matches(self):
        first = router.build_index(self.project, chunk_lines=10)
        second = router.build_index(self.project, chunk_lines=10)
        self.assertEqual(first, second)
        self.assertTrue(all(record["content_hash"] for record in first))
        results = router.search(first, "shop cancellation service use", limit=5)
        self.assertTrue(results)
        self.assertIn(results[0]["path"], {"know-how/ARCHITECTURE.md", "survivor/shop/service.gd"})
        self.assertTrue(results[0]["reasons"])

    def test_index_supports_non_godot_repository(self):
        (self.project / "project.godot").unlink()
        records = router.build_index(self.project, roots=["know-how"], chunk_lines=10)
        self.assertTrue(records)

    def test_related_tasks_are_grouped_without_dumping_task_text(self):
        records = router.build_index(self.project, chunk_lines=10)
        task_records = [record for record in records if record["task_record"]]
        self.assertTrue(task_records)
        self.assertEqual(task_records[0]["task_id"], "2026-09-01-shop-cancellation")
        related = router.related_tasks(records, "shop cancellation service use")
        self.assertEqual(related[0]["task_id"], "2026-09-01-shop-cancellation")
        self.assertIn("plan", related[0]["artifacts"])
        self.assertNotIn("text", related[0]["matches"][0])

    def test_previous_task_scope_limits_shadow_candidates(self):
        records = router.build_index(self.project, chunk_lines=10)
        scoped = router.search(records, "shop cancellation service use", 20, task_records_only=True)
        self.assertTrue(scoped)
        self.assertTrue(all(candidate["task_record"] for candidate in scoped))

    def test_index_rejects_escaping_root_and_symlink(self):
        with self.assertRaisesRegex(ValueError, "relative"):
            router.build_index(self.project, roots=["../outside"])
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.project / "know-how/link"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            router.build_index(self.project)

    def test_shadow_calls_api_once_per_candidate_and_writes_content_free_cache(self):
        records = router.build_index(self.project, chunk_lines=10)
        cache = self.project / "shadow-cache.jsonl"
        answers = {
            name: {"type": "noul", "noul": 0.9 if name in {"relevant", "needed_before_action"} else 0.1}
            for name in shadow.QUESTIONS
        }
        body = {"model": shadow.MODEL, "answers": answers}
        opener = mock.Mock()
        opener.open.return_value = FakeResponse(body)
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "secret"}), mock.patch.object(
            shadow.urllib.request, "build_opener", return_value=opener
        ):
            result = shadow.shadow(records, "shop cancellation service use", "tdd", cache, limit=1)
        self.assertEqual(opener.open.call_count, 1)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(result[0]["shadow_route"], "include")
        cache_text = cache.read_text()
        self.assertNotIn("secret", cache_text)
        self.assertNotIn("Shop cancellation", cache_text)

    def test_cached_shadow_needs_no_key_or_network(self):
        records = router.build_index(self.project, chunk_lines=10)
        candidate = router.search(records, "shop cancellation service use", 1)[0]
        task = "shop cancellation service use"
        key = shadow._cache_key(shadow.MODEL, task, "tdd", [], candidate)
        evaluation = {
            "model": shadow.MODEL,
            "answers": {name: 0.1 for name in shadow.QUESTIONS},
        }
        cache = self.project / "shadow-cache.jsonl"
        router.write_jsonl(cache, [{"cache_key": key, "evaluation": evaluation}])
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            shadow.urllib.request, "build_opener"
        ) as opened:
            result = shadow.shadow(records, task, "tdd", cache, limit=1)
        opened.assert_not_called()
        self.assertTrue(result[0]["cached"])

    def test_uncached_shadow_without_key_fails_before_network(self):
        records = router.build_index(self.project, chunk_lines=10)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            shadow.urllib.request, "build_opener"
        ) as opened:
            with self.assertRaisesRegex(RuntimeError, "TYPESAFE_API_KEY"):
                shadow.shadow(records, "shop cancellation", "tdd", self.project / "cache.jsonl", limit=1)
        opened.assert_not_called()

    def test_secure_writer_does_not_follow_predictable_temp_symlink(self):
        output = self.project / "index.jsonl"
        victim = self.project / "victim.txt"
        victim.write_text("keep me")
        (self.project / "index.jsonl.tmp").symlink_to(victim)
        router.write_jsonl(output, [{"safe": True}])
        self.assertEqual(victim.read_text(), "keep me")
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_root_file_symlink_is_rejected(self):
        outside = Path(self.temp.name) / "README.md"
        outside.write_text("outside")
        (self.project / "README.md").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            router.build_index(self.project)

    def test_symlinked_output_parent_is_rejected(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        linked_parent = self.project / ".context"
        linked_parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink in output"):
            router.write_jsonl(linked_parent / "index.jsonl", [{"safe": True}])
        self.assertEqual(list(outside.iterdir()), [])

    def test_api_model_mismatch_and_malformed_response_are_rejected(self):
        candidate = router.search(router.build_index(self.project, chunk_lines=10), "shop service", 1)[0]
        answers = {name: {"type": "noul", "noul": 0.5} for name in shadow.QUESTIONS}
        opener = mock.Mock()
        opener.open.return_value = FakeResponse({"model": "jev-other", "answers": answers})
        with mock.patch.object(shadow.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "unexpected model"):
                shadow._ask_jev("shop", "tdd", [], candidate, shadow.MODEL, "secret", 1)
        opener.open.return_value = FakeResponse({"model": shadow.MODEL, "answers": []})
        with mock.patch.object(shadow.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "malformed"):
                shadow._ask_jev("shop", "tdd", [], candidate, shadow.MODEL, "secret", 1)

    def test_malformed_cache_is_rejected_cleanly(self):
        cache = self.project / "cache.jsonl"
        cache.write_text('[]\n')
        with self.assertRaisesRegex(ValueError, "JSON object"):
            shadow.shadow([], "shop", "tdd", cache)
        cache.write_text('{"cache_key":"x","evaluation":{"model":"jev-1.13.0","answers":{}}}\n')
        with self.assertRaisesRegex(ValueError, "invalid relevant"):
            shadow.shadow([], "shop", "tdd", cache)

    def test_shadow_cli_requires_explicit_remote_consent(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(
                ["shadow", "--index", "i", "--task", "t", "--cache", "c"]
            )
        args = cli.build_parser().parse_args(
            ["shadow", "--index", "i", "--task", "t", "--cache", "c", "--allow-remote"]
        )
        self.assertTrue(args.allow_remote)

    def test_cache_model_mismatch_is_rejected(self):
        records = router.build_index(self.project, chunk_lines=10)
        candidate = router.search(records, "shop service", 1)[0]
        key = shadow._cache_key(shadow.MODEL, "shop service", "tdd", [], candidate)
        cache = self.project / "cache.jsonl"
        router.write_jsonl(
            cache,
            [{
                "cache_key": key,
                "evaluation": {
                    "model": "jev-other",
                    "answers": {name: 0.1 for name in shadow.QUESTIONS},
                },
            }],
        )
        with self.assertRaisesRegex(ValueError, "expected"):
            shadow.shadow(records, "shop service", "tdd", cache, limit=1)

    def test_malformed_heading_is_rejected_cleanly(self):
        records = router.build_index(self.project, chunk_lines=10)
        records[0]["heading"] = 42
        with self.assertRaisesRegex(ValueError, "malformed heading"):
            router.search(records, "shop", 1)

    def test_remote_opener_disables_redirects(self):
        candidate = router.search(router.build_index(self.project, chunk_lines=10), "shop service", 1)[0]
        opener = mock.Mock()
        opener.open.side_effect = shadow.urllib.error.HTTPError(
            shadow.ENDPOINT, 302, "Found", {}, None
        )
        with mock.patch.object(shadow.urllib.request, "build_opener", return_value=opener) as built:
            with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
                shadow._ask_jev("shop", "tdd", [], candidate, shadow.MODEL, "secret", 1)
        self.assertIsInstance(built.call_args.args[0], shadow._NoRedirectHandler)


if __name__ == "__main__":
    unittest.main()
