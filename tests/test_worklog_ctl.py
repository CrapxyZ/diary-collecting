"""Exercise the public CLI and failure paths that need deterministic injection."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "worklog_ctl.py"
sys.path.insert(0, str(SCRIPT.parent))
import worklog_ctl as ctl


EMPTY = "## LOG\n\n### WORK\n\n### METRICS\n\n### PITFALLS\n\n### DECISIONS"


def marker(value):
    return {"kind": "marker", "value": value}


def started(anchor="start-1", **extra):
    return {"topic": "定位 API 失败", "prior_ctx": ["Existing tests used a mock server."],
            "start_anchor": marker(anchor), **extra}


def row_log(row, section="WORK"):
    heading = f"### {section}"
    return EMPTY.replace(heading, f"{heading}\n{row}")


def identity(snapshot):
    return {"session_id": snapshot["session_id"], "expected_revision": snapshot["revision"]}


def edit(snapshot, new_anchor, log, **extra):
    return {"old_anchor": snapshot["last_sync_anchor"], "new_anchor": marker(new_anchor),
            "log": log, **extra}


class WorklogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.logs = self.root / "logs"
        self.cwd = self.root / "project"
        self.cwd.mkdir()
        self.env = {**os.environ, "WORKLOG_DIR": str(self.logs), "PYTHONUTF8": "1"}

    def cli(self, *args, payload=None, raw=None, expected=0):
        stdin = json.dumps(payload) if payload is not None else raw
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args], cwd=self.cwd, env=self.env,
            input=stdin or "", capture_output=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        if expected:
            self.assertEqual(result.stdout, "")
            return json.loads(result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def start(self, slug="api", key="default", token="start-1", **extra):
        return self.cli("init", slug, "--key", key, payload=started(token, **extra))

    def test_lifecycle_preserves_prior_and_stable_ids(self):
        self.assertIsNone(self.cli("status")["active"])
        self.assertEqual(self.cli("get-active", expected=3)["error"], "no_active")
        first = self.start()
        self.assertEqual(self.cli("init", "another", payload=started("other"), expected=4)["error"],
                         "conflict")
        self.assertEqual(self.start()["result"], "already_active")
        snap = self.cli("get-active")
        self.assertEqual(snap["last_sync_anchor"], marker("start-1"))
        self.assertIn("## PRIOR_CTX\nExisting tests used a mock server.\n", snap["log"])

        first_log = row_log(r"T1 [DOING] investigate x \| y | src\\api.py | reproduced | -")
        changed = self.cli("update", payload={**identity(snap), **edit(snap, "updated-1", first_log)})
        self.assertEqual(changed["result"], "updated")
        after = self.cli("get-active")
        self.assertEqual(changed["revision"], after["revision"])
        self.assertIn(first_log, after["log"])
        self.assertIn("Existing tests used a mock server.", after["log"])
        self.assertEqual(after["last_sync_anchor"], marker("updated-1"))

        unchanged = self.cli("update", payload={
            **identity(after), **edit(after, "unused", first_log.replace("\n\n", "\n")),
        })
        self.assertEqual(unchanged["last_sync_anchor"], marker("updated-1"))
        self.assertEqual(unchanged["revision"], after["revision"])
        self.assertFalse(unchanged["committed"])

        self.cli("update", payload={**identity(after), **edit(after, "delete", EMPTY)}, expected=5)
        attempted_prior = {**identity(after), **edit(after, "other", first_log), "prior_ctx": []}
        self.cli("update", payload=attempted_prior, expected=5)
        final_log = row_log("T1 [DONE] fixed parser | src/api.py | tests pass | DOING -> DONE")
        ended = self.cli("freeze", payload={
            **identity(after), "final_update": edit(after, "end-1", final_log, project="Display"),
        })
        self.assertEqual(ended["status"], "frozen")
        self.assertEqual(ended["project_id"], first["project_id"])
        self.assertEqual(ended["project"], "Display")
        self.assertIn(final_log, Path(ended["path"]).read_text(encoding="utf-8"))
        self.assertIsNone(self.cli("status")["active"])
        self.assertEqual(self.cli("update", payload={
            **identity(after), **edit(after, "late", first_log),
        }, expected=4)["error"], "frozen")

        second = self.start(token="start-2")
        self.assertEqual(second["file"], f"{first['date']}_api_2.md")
        second_body = self.cli("get-active")["log"]
        self.assertEqual(second_body.split("## LOG", 1)[1].strip(), EMPTY.split("## LOG", 1)[1].strip())
        self.assertIn(f'"file": "{first["file"]}"', second_body)
        state = json.loads((self.logs / ".state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["frozen"], [first["file"]])
        self.assertEqual(state["active"][second["worktree_id"]]["default"], second["file"])

    def test_invalid_input_cannot_change_log(self):
        self.cli("init", "../outside", payload=started(), expected=5)
        self.cli("init", "invalid", payload=started(prior_ctx=["a", "b", "c", "d"]), expected=5)
        self.cli("init", "invalid", raw='{"topic":"a","topic":"b"}', expected=5)
        self.cli("list-by-date", "2026-02-30", expected=5)
        self.start()
        snap = self.cli("get-active")
        cases = [
            edit(snap, "bad", row_log("T2 [DONE] task | - | - | -")),
            edit(snap, "bad", row_log(r"T1 [DONE] illegal \q | - | - | -")),
            edit(snap, "bad", row_log("T1 [DONE] bare | pipe | - | - | -")),
            edit(snap, "bad", row_log("P1 [DONE] wrong section | - | - | -")),
            edit(snap, "bad", "## PRIOR_CTX\nmodified\n\n" + EMPTY),
            edit(snap, "start-1", row_log("T1 [DONE] task | - | - | -")),
        ]
        for invalid in cases:
            with self.subTest(log=invalid["log"], anchor=invalid["new_anchor"]):
                self.cli("update", payload={**identity(snap), **invalid}, expected=5)
        wrong_anchor = edit(snap, "valid", row_log("T1 [DONE] task | - | - | -"))
        wrong_anchor["old_anchor"] = marker("unknown")
        self.cli("update", payload={**identity(snap), **wrong_anchor}, expected=4)
        self.assertEqual(self.cli("get-active")["revision"], snap["revision"])

    def test_two_writers_with_same_revision_cannot_both_commit(self):
        first = self.start()
        snap = self.cli("get-active")
        args = [sys.executable, str(SCRIPT), "update"]
        payloads = [
            {**identity(snap), **edit(snap, f"anchor-{i}", row_log(f"T1 [DONE] writer {i} | - | - | -"))}
            for i in (1, 2)
        ]
        processes = []
        try:
            for payload in payloads:
                child = subprocess.Popen(args, cwd=self.cwd, env=self.env, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                processes.append(child)
                child.stdin.write(json.dumps(payload).encode("utf-8"))
                child.stdin.close()
                child.stdin = None
            outcomes = [proc.communicate(timeout=10) for proc in processes]
        finally:
            for child in processes:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
        self.assertEqual(sorted(proc.returncode for proc in processes), [0, 4], outcomes)
        saved = self.cli("get-active")
        self.assertEqual(saved["session_id"], first["session_id"])
        self.assertEqual(sum(f"writer {i}" in saved["log"] for i in (1, 2)), 1)
        self.cli("freeze", payload=identity(snap), expected=4)

    def test_rebuild_cache_and_fail_safe_on_duplicate_active_scope(self):
        first = self.start()
        cache = self.logs / ".state.json"
        for stale in ('{"active":{}}', "{broken", None):
            if stale is None:
                cache.unlink()
            else:
                cache.write_text(stale, encoding="utf-8")
            self.assertEqual(self.cli("status")["active"]["session_id"], first["session_id"])
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["active"]
                             [first["worktree_id"]]["default"], first["file"])
        duplicate = self.logs / f"{first['date']}_duplicate.md"
        shutil.copyfile(first["path"], duplicate)
        error = self.cli("init", "--key", "other", payload=started("second"), expected=5)
        self.assertIn("duplicate active scope", error["issues"][0]["message"])

    def test_cache_failure_reports_successful_log_commit(self):
        with patch.dict(os.environ, self.env):
            store = ctl.Store()
            scope = ctl.scope_for(self.cwd, "default")
            original_write = ctl.atomic_write

            def fail_cache(path, data):
                if path == store.state:
                    raise OSError("simulated cache failure")
                return original_write(path, data)

            with patch.object(ctl, "atomic_write", side_effect=fail_cache):
                first = store.init(scope, "api", False, started())
            self.assertTrue(first["committed"])
            self.assertIn("simulated cache failure", first["warnings"][0])
            self.assertEqual(store.inspect("status", scope)["active"]["session_id"], first["session_id"])
            self.assertTrue(store.state.is_file())

    def test_auto_end_does_not_publish_new_log_when_freeze_sync_fails(self):
        first = self.start()
        with patch.dict(os.environ, self.env):
            store = ctl.Store()
            scope = ctl.scope_for(self.cwd, "default")
            original_publish = ctl.publish

            def warn_on_freeze(temporary, path):
                result = original_publish(temporary, path)
                if str(path) == first["path"]:
                    return "simulated directory sync failure"
                return result

            payload = {**started("next"), **identity(first)}
            with patch.object(ctl, "publish", side_effect=warn_on_freeze):
                with self.assertRaises(ctl.WorklogError) as caught:
                    store.init(scope, "next", True, payload)
            self.assertEqual(caught.exception.code, "partial_failure")
            self.assertIn("simulated directory sync failure", caught.exception.details["warnings"])
            self.assertIsNone(store.inspect("status", scope)["active"])
            self.assertEqual([item.name for item in self.logs.glob("*.md")], [first["file"]])

    def test_auto_end_retries_and_partial_publish_recovery(self):
        first = self.start()
        self.cli("init", "next", "--auto-end", payload={
            **started("next"), **identity(first), "expected_revision": "0" * 64,
        }, expected=4)
        self.assertEqual(self.cli("get-active")["session_id"], first["session_id"])
        with patch.dict(os.environ, self.env):
            store = ctl.Store()
            scope = ctl.scope_for(self.cwd, "default")
            original_publish = ctl.publish

            def fail_new_session(temporary, path):
                if path.name.endswith("_broken.md"):
                    raise OSError("simulated error before publishing the new session")
                return original_publish(temporary, path)

            with patch.object(ctl, "publish", side_effect=fail_new_session):
                with self.assertRaises(ctl.WorklogError) as caught:
                    store.init(scope, "broken", True, {**started("broken"), **identity(first)})
            self.assertEqual(caught.exception.code, "partial_failure")
            self.assertEqual(caught.exception.details["frozen"]["session_id"], first["session_id"])
            self.assertIsNone(store.inspect("status", scope)["active"])
            self.assertEqual(list(self.logs.glob(".worklog-*.tmp")), [])

        recovery = self.start("recovered", token="broken")
        payload = {**started("next"), **identity(recovery)}
        next_ = self.cli("init", "next", "--auto-end", payload=payload)
        duplicate = self.cli("init", "next", "--auto-end", payload=payload)
        self.assertEqual(next_["frozen"]["session_id"], recovery["session_id"])
        self.assertEqual(duplicate["session_id"], next_["session_id"])
        self.assertEqual(duplicate["result"], "already_active")
        self.assertEqual(self.cli("status")["active"]["session_id"], next_["session_id"])

    def test_cross_midnight_stays_on_start_date_and_explicit_file_can_be_collected(self):
        offset = timezone(timedelta(hours=8))
        yesterday = datetime(2026, 11, 27, 23, 58, tzinfo=offset)
        today = yesterday + timedelta(minutes=10)
        with patch.dict(os.environ, self.env):
            store = ctl.Store()
            scope = ctl.scope_for(self.cwd, "default")
            with patch.object(ctl, "now", return_value=yesterday):
                first = store.init(scope, "overnight", False, started())
            snap = store.inspect("get-active", scope)
            with patch.object(ctl, "now", return_value=today):
                updated = store.change(scope, {
                    **identity(snap), **edit(snap, "tomorrow", row_log("T1 [DONE] second day | - | - | -")),
                }, False)
            self.assertEqual(updated["file"], "2026-11-27_overnight.md")
            self.assertIn('updated_at: "2026-11-28T00:08:00+08:00"', store.inspect("get-active", scope)["log"])
        self.assertEqual(self.cli("list-by-date", "2026-11-28")["candidates"], [])
        explicit = self.cli("collect", "2026-11-28", "--file", first["path"])
        self.assertEqual(explicit["sources"][0]["date"], "2026-11-27")

    def test_candidates_and_key_collection_do_not_open_unselected_body(self):
        api = self.start(key="api", token="api-start")
        tests = self.start("tests", key="tests", token="test-start")
        private = self.start("private", key="private", token="private-start")
        secret = Path(private["path"])
        data = secret.read_bytes()
        secret.write_bytes(data[:data.index(b"## PRIOR_CTX")] + b"\xffmalformed private body")
        candidates = self.cli("list-by-date", api["date"])["candidates"]
        self.assertEqual({candidate["session_key"] for candidate in candidates}, {"api", "tests", "private"})
        self.assertTrue(all("log" not in candidate for candidate in candidates))
        self.cli("collect", api["date"], expected=2)
        report = self.cli("collect", api["date"], "--key", "api", "--file", tests["path"])
        self.assertEqual({item["file"] for item in report["sources"]}, {api["file"], tests["file"]})
        failure = self.cli("collect", api["date"], "--all", expected=5)
        self.assertEqual(failure["issues"][0]["path"], private["path"])
        self.assertEqual(self.cli("status", "--key", "api")["active"]["session_id"], api["session_id"])

    def test_explicit_paths_deduplicate_and_report_conflicting_versions(self):
        first = self.start()
        copy = self.root / "restored.md"
        shutil.copyfile(first["path"], copy)
        same = self.cli("collect", first["date"], "--file", first["path"], "--file", str(copy))
        self.assertEqual(len(same["sources"]), 1)
        copy.write_text(copy.read_text(encoding="utf-8").replace("Existing tests", "Previous tests"),
                        encoding="utf-8")
        conflict = self.cli("collect", first["date"], "--file", first["path"], "--file", str(copy), expected=5)
        self.assertIn("same session_id", conflict["issues"][0]["message"])
        self.cli("collect", first["date"], "--file", str(self.root / "missing.md"), expected=5)

    @unittest.skipUnless(shutil.which("git"), "git is optional")
    def test_worktree_scope_and_project_grouping(self):
        def git(*args):
            return subprocess.run(["git", "-C", str(self.cwd), *args], check=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        git("init")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "--allow-empty", "-m", "initial")
        nested = self.cwd / "nested"
        nested.mkdir()
        worktree = self.root / "linked"
        git("worktree", "add", "--detach", str(worktree))
        first = self.start(key="api")
        self.assertEqual(self.cli("get-active", "--cwd", str(nested), "--key", "api")["session_id"],
                         first["session_id"])
        second = self.cli("init", "linked", "--cwd", str(worktree), "--key", "api",
                          payload=started("worktree-start"))
        self.assertNotEqual(first["worktree_id"], second["worktree_id"])
        self.assertEqual(first["project_id"], second["project_id"])
        another_same_name = self.root / "other" / self.cwd.name
        another_same_name.mkdir(parents=True)
        another = self.cli("init", "other", "--cwd", str(another_same_name), "--key", "api",
                           payload=started("other-start"))
        self.assertEqual(another["project"], first["project"])
        self.assertNotEqual(another["project_id"], first["project_id"])

        error = self.cli("collect", first["date"], "--key", "api", expected=4)
        self.assertEqual(len(error["candidates"]), 3)
        all_keys = self.cli("collect", first["date"], "--key", "api", "--all-matching-keys")
        self.assertEqual(len(all_keys["sources"]), 3)

    def test_save_brief_requires_confirmation_for_different_content(self):
        title = "# 每日简报\n"
        first = self.cli("save-brief", "2026-11-27", raw=title)
        file = self.logs / "briefs" / "2026-11-27_brief.md"
        self.assertEqual(first["path"], str(file))
        self.assertEqual(file.read_text(encoding="utf-8"), title)
        self.assertEqual(self.cli("save-brief", "2026-11-27", raw=title)["result"], "unchanged")
        self.cli("save-brief", "2026-11-27", raw="# revised\n", expected=4)
        self.assertEqual(file.read_text(encoding="utf-8"), title)
        self.cli("save-brief", "2026-11-27", "--overwrite", raw="# revised\n")
        self.assertEqual(file.read_text(encoding="utf-8"), "# revised\n")

    def test_json_is_utf8_independently_of_stdio_encoding(self):
        self.env["PYTHONIOENCODING"] = "ascii"
        result = self.start()
        self.assertEqual(result["topic"], "定位 API 失败")
        self.assertEqual(self.cli("get-active")["log"].count("定位 API 失败"), 1)


if __name__ == "__main__":
    unittest.main()
