"""Check Re-pin Receipt with disposable, synthetic Git repositories."""
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


CLI = Path(__file__).resolve().parents[1] / "repin-receipt"


class Corpus:
    """Create an isolated commit history with ordinary Git commands."""

    def __init__(self, folder, object_format="sha1"):
        """Initialize a small local history in the requested Git object format."""
        self.root = folder / "repo"
        self.root.mkdir()
        self.scope = folder / "reviewed.txt"
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.git("init", "-q", "--object-format=" + object_format)
        self.git("config", "user.name", "Simulated Reviewer")
        self.git("config", "user.email", "reviewer@example.invalid")
        self.write("core/one.txt", b"A\n")
        self.write("core/café and [draft]*.bin", b"\x00\xffSYNTHETIC_CONTENT\n")
        self.write("keep.txt", b"outside\n")
        self.old = self.commit("Initial simulated review")

    def git(self, *args, data=None):
        """Run a local Git fixture command and return its stdout bytes."""
        result = subprocess.run(["git", "-C", str(self.root), *args], input=data, env=self.env,
                                capture_output=True, check=True)
        return result.stdout.strip()

    def write(self, path, data):
        """Write a synthetic worktree file."""
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def commit(self, label, *, stage=True):
        """Record one fixture tree and return its full commit OID."""
        if stage:
            self.git("add", "--all")
        self.git("commit", "-qm", label, "--allow-empty")
        return self.git("rev-parse", "HEAD").decode("ascii")

    def run(self, new, scope=b"core/\n", *, old=None, receipt="simulated-review-1", as_json=True, env=None):
        """Call the real draft CLI on one literal scope fixture."""
        self.scope.write_bytes(scope)
        args = [sys.executable, str(CLI), "--repo", str(self.root), "--old", self.old if old is None else old,
                "--new", new, "--receipt", receipt, "--scope", str(self.scope)]
        if as_json:
            args.append("--json")
        return subprocess.run(args, capture_output=True, env=self.env if env is None else env, check=False)


class ReceiptTests(unittest.TestCase):
    """Exercise the receipt contract through the executable CLI."""

    def setUp(self):
        """Use a fresh synthetic repository for every test."""
        directory = tempfile.TemporaryDirectory(prefix="repin-receipt-test-")
        self.addCleanup(directory.cleanup)
        self.corpus = Corpus(Path(directory.name))

    def result(self, run, code):
        """Check a successful receipt exit and decode its UTF-8 JSON."""
        self.assertEqual(run.returncode, code, run.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(run.stderr, b"")
        return json.loads(run.stdout)

    def invalid(self, run, reason):
        """Require an invalid-evidence exit with no partial receipt or absolute fixture path."""
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, b"")
        self.assertIn(reason.encode(), run.stderr)
        self.assertNotIn(os.fsencode(self.corpus.root), run.stderr)

    def test_identical_scope_and_outside_change(self):
        """An outside-only change leaves full in-scope entry evidence and exit zero."""
        self.corpus.write("keep.txt", b"changed outside\n")
        new = self.corpus.commit("Change outside")
        data = self.result(self.corpus.run(new), 0)
        self.assertEqual(data["old_commit"], self.corpus.old)
        self.assertEqual(data["new_commit"], new)
        self.assertEqual(data["receipt_id"], "simulated-review-1")
        self.assertEqual(data["object_format"], "sha1")
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["scope"], ["core/"])
        self.assertFalse(data["scope_changed"])
        self.assertEqual(data["outside_scope_changed_paths"], ["keep.txt"])
        self.assertEqual([row["path"] for row in data["scoped"]],
                         ["core/café and [draft]*.bin", "core/one.txt"])
        for row in data["scoped"]:
            self.assertEqual(row["status"], "unchanged")
            self.assertEqual(row["old"], row["new"])
            self.assertEqual(set(row["new"]), {"mode", "type", "oid"})
        same = self.result(self.corpus.run(self.corpus.old), 0)
        self.assertEqual(same["outside_scope_changed_paths"], [])

    def test_changes_add_delete_and_boundary_move(self):
        """Count a content change, additions and a move leaving the declared scope."""
        self.corpus.write("core/one.txt", b"B\n")
        oldname = "core/café and [draft]*.bin"
        (self.corpus.root / oldname).rename(self.corpus.root / "moved outside.bin")
        self.corpus.write("core/new item.txt", b"NEW\n")
        new = self.corpus.commit("Move and change")
        run = self.corpus.run(new)
        data = self.result(run, 1)
        self.assertTrue(data["scope_changed"])
        self.assertEqual(data["outside_scope_changed_paths"], ["moved outside.bin"])
        by_path = {row["path"]: row for row in data["scoped"]}
        self.assertEqual(by_path[oldname]["status"], "deleted")
        self.assertIsNone(by_path[oldname]["new"])
        self.assertEqual(by_path["core/new item.txt"]["status"], "added")
        self.assertIsNone(by_path["core/new item.txt"]["old"])
        row = by_path["core/one.txt"]
        self.assertEqual(row["status"], "changed")
        self.assertNotEqual(row["old"]["oid"], row["new"]["oid"])
        self.assertNotIn(b"SYNTHETIC_CONTENT", run.stdout)

    def test_literal_paths_and_exact_directory_prefixes(self):
        """Pattern punctuation is literal and a scope does not cover prefix lookalikes."""
        path = "core/café and [draft]*.bin"
        self.corpus.write(path, b"content changed\n")
        self.corpus.write("core-other/one.txt", b"lookalike directory\n")
        new = self.corpus.commit("Literal path")
        report = self.result(self.corpus.run(new, (path + "\r\n").encode()), 1)
        self.assertEqual([row["path"] for row in report["scoped"]], [path])
        self.assertEqual(report["outside_scope_changed_paths"], ["core-other/one.txt"])

    def test_mode_symlink_and_gitlink_metadata(self):
        """Compare metadata directly from committed entries, including cross-type changes."""
        self.corpus.git("update-index", "--chmod=+x", "core/one.txt")
        blob = self.corpus.git("hash-object", "-w", "--stdin", data=b"one.txt").decode()
        self.corpus.git("update-index", "--add", "--cacheinfo", "120000," + blob + ",core/link")
        self.corpus.git("update-index", "--add", "--cacheinfo", "160000," + self.corpus.old + ",core/vendor")
        new = self.corpus.commit("Modes and links", stage=False)
        data = self.result(self.corpus.run(new), 1)
        by_path = {row["path"]: row for row in data["scoped"]}
        self.assertEqual(by_path["core/one.txt"]["old"]["mode"], "100644")
        self.assertEqual(by_path["core/one.txt"]["new"]["mode"], "100755")
        self.assertEqual(by_path["core/one.txt"]["old"]["oid"], by_path["core/one.txt"]["new"]["oid"])
        self.assertEqual(by_path["core/link"]["new"], {"mode": "120000", "type": "blob", "oid": blob})
        self.assertEqual(by_path["core/vendor"]["new"], {"mode": "160000", "type": "commit", "oid": self.corpus.old})

    def test_merge_rebase_and_dirty_config_are_endpoint_only(self):
        """Compare exact trees across history shapes and leave the live index and references intact."""
        tree = self.corpus.git("rev-parse", self.corpus.old + "^{tree}").decode()
        side = self.corpus.git("commit-tree", tree, "-m", "Independent history").decode()
        merge = self.corpus.git("commit-tree", tree, "-p", self.corpus.old, "-p", side,
                                "-m", "Merge with the same tree").decode()
        clean = self.corpus.run(merge)
        data = self.result(clean, 0)
        alternate = self.result(self.corpus.run(side), 0)
        self.assertEqual(data["scoped"], alternate["scoped"])
        self.corpus.write("core/one.txt", b"staged fixture\n")
        self.corpus.git("add", "core/one.txt")
        self.corpus.write("core/one.txt", b"dirty fixture\n")
        self.corpus.git("config", "diff.renames", "true")
        self.corpus.git("config", "core.quotePath", "false")
        index = self.corpus.root / ".git/index"
        before = index.read_bytes(), self.corpus.git("show-ref")
        again = self.corpus.run(merge)
        self.assertEqual(again.stdout, clean.stdout)
        self.assertEqual((index.read_bytes(), self.corpus.git("show-ref")), before)

    def test_replace_refs_are_ignored(self):
        """A local replacement ref cannot substitute for the pinned commit."""
        self.corpus.write("core/one.txt", b"Different tree\n")
        new = self.corpus.commit("Replacement tree")
        before = self.corpus.run(new)
        self.result(before, 1)
        self.corpus.git("replace", self.corpus.old, new)
        after = self.corpus.run(new)
        self.assertEqual(after.stdout, before.stdout)
        self.assertEqual(after.returncode, 1)

    def test_git_environment_does_not_redirect_repo(self):
        """The explicitly selected repository determines the compared commit trees."""
        before = self.corpus.run(self.corpus.old)
        self.result(before, 0)
        env = {**self.corpus.env, "GIT_DIR": str(self.corpus.root / "missing.git")}
        after = self.corpus.run(self.corpus.old, env=env)
        self.assertEqual(after.stdout, before.stdout)
        self.assertEqual(after.returncode, 0)

    def test_invalid_scope_entries(self):
        """Unmatched, ambiguous or malformed lines return exit two."""
        for content, diagnostic in [
            (b"", "scope"), (b"\xff\n", "UTF-8"), (b"\n", "scope"),
            (b"/absolute\n", "relative"), (b"C:/absolute\n", "relative"), (b"core/../keep.txt\n", "relative"),
            (b"core/\ncore/\n", "duplicate"), (b"core/\ncore/one.txt\n", "overlap"),
            (b"core\n", "match"), (b"unmatched/\n", "match"),
        ]:
            with self.subTest(content=content):
                self.invalid(self.corpus.run(self.corpus.old, content), diagnostic)

    def test_invalid_commit_or_receipt(self):
        """Refuse short IDs, missing commits, wrong object types and malformed receipt IDs."""
        oid = self.corpus.old
        self.invalid(self.corpus.run(oid, old=oid[:8]), "full")
        self.invalid(self.corpus.run("0" * len(oid)), "commit")
        blob = self.corpus.git("rev-parse", oid + ":core/one.txt").decode()
        self.invalid(self.corpus.run(oid, old=blob), "commit")
        self.invalid(self.corpus.run(oid, receipt="line\nbreak"), "printable")

    def test_endpoint_ambiguity_diagnostic_refuses_without_receipt(self):
        """A successful Git inspection with an ambiguity warning is still invalid evidence."""
        tool = runpy.run_path(str(CLI))
        old = self.corpus.old
        new = self.corpus.commit("Independent pin")
        blob = self.corpus.git("rev-parse", old + ":core/one.txt")
        self.corpus.scope.write_text("core/one.txt\n")
        argv = [str(CLI), "--repo", str(self.corpus.root), "--old", old, "--new", new,
                "--scope", str(self.corpus.scope), "--receipt", "simulated-ambiguity", "--json"]
        for warned in (old, new):
            with self.subTest(endpoint=warned):
                calls = []

                def diagnostic(command, **options):
                    """Supply an inert simulated Git diagnostic and otherwise valid metadata."""
                    calls.append(command)
                    warning = b"warning: refname 'SIMULATED_ENDPOINT' is ambiguous.\n"
                    if "rev-parse" in command:
                        return subprocess.CompletedProcess(command, 0, b"sha1\n", b"")
                    if "cat-file" in command:
                        return subprocess.CompletedProcess(command, 0, b"commit\n",
                                                           warning if command[-1] == warned else b"")
                    return subprocess.CompletedProcess(command, 0, b"100644 blob " + blob + b"\tcore/one.txt\0", b"")

                stdout, stderr = io.BytesIO(), io.StringIO()
                with mock.patch("subprocess.run", side_effect=diagnostic), mock.patch("sys.argv", argv), \
                        mock.patch("sys.stderr", stderr), mock.patch("sys.stdout", mock.Mock(buffer=stdout)):
                    code = tool["main"]()
                self.assertEqual(code, 2)
                self.assertEqual(stdout.getvalue(), b"")
                self.assertIn("ambiguous", stderr.getvalue())
                self.assertNotIn("SIMULATED_ENDPOINT", stderr.getvalue())
                self.assertTrue(all("core.warnAmbiguousRefs=true" in command for command in calls))

    def test_unavailable_tree_is_invalid_evidence(self):
        """A pinned commit with a missing tree produces no receipt."""
        absent = "f" * len(self.corpus.old)
        content = (f"tree {absent}\nauthor Simulated <reviewer@example.invalid> 1000000000 +0000\n"
                   "committer Simulated <reviewer@example.invalid> 1000000000 +0000\n\nMissing tree\n").encode()
        broken = self.corpus.git("hash-object", "-t", "commit", "-w", "--stdin", data=content).decode()
        self.invalid(self.corpus.run(broken), "tree")

    def test_directory_without_slash_names_the_trailing_slash(self):
        """A directory written without a trailing slash tells the reader to add one."""
        self.invalid(self.corpus.run(self.corpus.old, b"core\n"), "add a trailing / for a directory")
        missing = self.corpus.run(self.corpus.old, b"missing\n")
        self.invalid(missing, "match")
        self.assertNotIn(b"add a trailing / for a directory", missing.stderr)
        self.invalid(self.corpus.run(self.corpus.old, b"# comment\n"), "start with #")

    def test_help_and_readme_list_the_reader_facts(self):
        """Help and the README name the flags, statuses and refusals a first reader hits."""
        help_run = subprocess.run([sys.executable, str(CLI), "--help"], capture_output=True, check=False)
        self.assertEqual(help_run.returncode, 0, help_run.stderr.decode())
        help_text = help_run.stdout.decode()
        readme = (CLI.parent / "README.md").read_text(encoding="utf-8")
        for fact in ("--repo", "--json", "--version", "added", "deleted", "(absent)",
                     "blank", "paths with a . or .. component", "start with #",
                     "non-UTF-8 path refuses the run"):
            with self.subTest(fact=fact):
                self.assertIn(fact, help_text)
                self.assertIn(fact, readme)

    def test_text_receipt_and_sha256_repository(self):
        """Read the text result and support full SHA-256 commit IDs."""
        text = self.corpus.run(self.corpus.old, as_json=False)
        self.assertEqual(text.returncode, 0)
        self.assertIn(b"scope unchanged", text.stdout)
        self.assertIn(self.corpus.old.encode(), text.stdout)
        with tempfile.TemporaryDirectory(prefix="repin-sha256-") as folder:
            repo = Corpus(Path(folder), object_format="sha256")
            data = self.result(repo.run(repo.old), 0)
            self.assertEqual(data["object_format"], "sha256")
            self.assertEqual(len(data["old_commit"]), 64)


if __name__ == "__main__":
    unittest.main()
