"""Read-only, fail-closed checks for the repository handover command."""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "handover.py"
spec = importlib.util.spec_from_file_location("rvv_handover", SCRIPT)
assert spec is not None and spec.loader is not None
handover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handover)

REPO = "region-viken-vest-hockey/hockey"
SEASON = "2026-2027"
MAIN = "a" * 40
PAGES = "b" * 40
PUBLISHED = "old-publication-revision"
CURRENT = "new-working-canonical-revision"


class ReadOnlyRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.ci = "success"
        self.public_revision = PUBLISHED
        self.lifecycle_ok = True
        self.available = True
        self.comments = 0
        self.pages_reads = 0
        self.change_pages_midflight = False

    def __call__(self, args: list[str], _root: Path) -> str:
        self.calls.append(tuple(args))
        assert args[0] in {"git", "gh"} or args[1:4] == [
            "season", "lifecycle", "--season"
        ]
        if args[:2] == ["git", "branch"]:
            return "main"
        if args[:2] == ["git", "rev-parse"]:
            return MAIN
        if args[:2] == ["git", "status"]:
            return ""
        if args[:2] == ["git", "log"]:
            return "a123456 recent implementation"
        if args[0] != "gh":
            return json.dumps({
                "state": "published_sealed",
                "canonical_state_revision": CURRENT,
                "published_baseline": {
                    "publication_id": "2026-09-21T0908",
                    "canonical_revision": PUBLISHED,
                },
                "reconciliation": {"ok": self.lifecycle_ok, "unexplained_delta": {}},
            })
        if not self.available:
            raise OSError("no authenticated Github client")
        endpoint = args[2]
        if endpoint.endswith("branches/main"):
            return json.dumps({"commit": {"sha": MAIN}})
        if endpoint.endswith("branches/gh-pages"):
            self.pages_reads += 1
            revision = "c" * 40 if self.change_pages_midflight and self.pages_reads > 1 else PAGES
            return json.dumps({"commit": {"sha": revision}})
        if "/actions/runs?" in endpoint:
            return json.dumps({"workflow_runs": [
                {"name": "CI", "status": "completed", "conclusion": self.ci,
                 "head_sha": MAIN, "html_url": "https://github.com/example/actions/runs/1"}
            ]})
        if "/contents/latest/index.html?" in endpoint:
            html = f'<meta name="season-revision" content="{self.public_revision}">'
            return json.dumps({"content": base64.b64encode(html.encode()).decode()})
        if "/issues?" in endpoint:
            return json.dumps([
                {"number": 12, "title": "Live issue", "html_url": "https://github.com/example/issues/12"},
                {"number": 13, "pull_request": {}, "title": "Not an issue"},
            ])
        if "/issues/12/comments?" in endpoint:
            return json.dumps([{
                "body": "Last verified SHA: old; recheck against current main.",
                "created_at": "2026-09-24T07:00:00Z",
                "html_url": "https://github.com/example/issues/12#issuecomment-1",
            }])
        if endpoint.endswith("/issues/12"):
            return json.dumps({
                "number": 12, "title": "Live issue", "state": "open",
                "html_url": "https://github.com/example/issues/12",
                "comments": self.comments,
            })
        raise AssertionError(f"unexpected read-only query: {endpoint}")


def _root(tmp_path: Path) -> Path:
    manifest = tmp_path / "export" / "2026-09-21T0908" / "export_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "export_id": "2026-09-21T0908",
        "lifecycle_status": "published",
        "canonical_season": SEASON,
        "canonical_revision": PUBLISHED,
        "export_fingerprint": "export-fingerprint",
        "generated_at": "2026-09-21T09:14:53+00:00",
        "published_at": "2026-09-21T09:14:53+00:00",
    }), encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    return tmp_path


def test_handover_distinguishes_publication_and_unpublished_canonical(tmp_path: Path) -> None:
    root = _root(tmp_path)
    before = (root / "export" / "2026-09-21T0908" / "export_manifest.json").read_bytes()
    runner = ReadOnlyRunner()
    report = handover.collect(root=root, season=SEASON, repo=REPO, issue=12, runner=runner)
    assert report["verdict"] == "CONTEXT_VERIFIED"
    assert report["publish_ready"] is False
    assert report["published"]["canonical_revision"] == PUBLISHED
    assert report["canonical"]["canonical_state_revision"] == CURRENT
    assert report["canonical"]["reconciliation"]["ok"] is True
    assert report["active_issue"]["number"] == 12
    assert len(report["recent_open_issues"]) == 1
    assert report["audit"]["status"] == "not_verified"
    assert (root / "export" / "2026-09-21T0908" / "export_manifest.json").read_bytes() == before
    assert all("publish" not in " ".join(call) for call in runner.calls)


def test_publication_mismatch_requires_review(tmp_path: Path) -> None:
    runner = ReadOnlyRunner()
    runner.public_revision = "unexpected"
    report = handover.collect(root=_root(tmp_path), season=SEASON, runner=runner)
    assert report["verdict"] == "REVIEW_REQUIRED"
    assert any("Public latest revision" in row for row in report["risks"])


def test_published_manifest_is_scoped_to_requested_season(tmp_path: Path) -> None:
    root = _root(tmp_path)
    other = tmp_path / "export" / "2025-2026T0001" / "export_manifest.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps({
        "export_id": "2025-2026T0001",
        "lifecycle_status": "published",
        "canonical_season": "2025-2026",
        "canonical_revision": "other-season-revision",
        "generated_at": "2030-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    report = handover.collect(root=root, season=SEASON, repo=REPO, runner=ReadOnlyRunner())
    assert report["published"]["export_id"] == "2026-09-21T0908"
    assert report["published"]["canonical_revision"] == PUBLISHED


def test_unreconciled_baseline_requires_review(tmp_path: Path) -> None:
    runner = ReadOnlyRunner()
    runner.lifecycle_ok = False
    report = handover.collect(root=_root(tmp_path), season=SEASON, runner=runner)
    assert report["verdict"] == "REVIEW_REQUIRED"
    assert any("does not reconcile" in row for row in report["risks"])


def test_failed_ci_or_unavailable_remote_never_claims_verified(tmp_path: Path) -> None:
    root = _root(tmp_path)
    runner = ReadOnlyRunner()
    runner.ci = "failure"
    report = handover.collect(root=root, season=SEASON, runner=runner)
    assert report["verdict"] == "REVIEW_REQUIRED"
    runner.available = False
    report = handover.collect(root=root, season=SEASON, runner=runner)
    assert report["verdict"] == "REVIEW_REQUIRED"
    assert report["github"]["main_head"] is None
    assert report["publish_ready"] is False


def test_incomplete_ci_requires_review(tmp_path: Path) -> None:
    runner = ReadOnlyRunner()
    runner.ci = None
    report = handover.collect(root=_root(tmp_path), season=SEASON, runner=runner)
    assert report["verdict"] == "REVIEW_REQUIRED"
    assert any("has not completed" in row for row in report["risks"])


def test_no_remote_still_reports_local_canonical_with_unknown_public(tmp_path: Path) -> None:
    runner = ReadOnlyRunner()
    report = handover.collect(root=_root(tmp_path), season=SEASON, runner=runner, remote=False)
    assert report["canonical"]["state"] == "published_sealed"
    assert report["github"]["gh_pages_head"] is None
    assert report["verdict"] == "REVIEW_REQUIRED"
    assert not any(call[0] == "gh" for call in runner.calls)


def test_no_manifest_or_lifecycle_must_fail_closed(tmp_path: Path) -> None:
    runner = ReadOnlyRunner()

    def without_lifecycle(args: list[str], root: Path) -> str:
        if args[0].endswith("rvv-miniputt"):
            raise RuntimeError("read failed")
        return runner(args, root)

    report = handover.collect(root=tmp_path, season=SEASON, runner=without_lifecycle)
    assert report["published"] is None
    assert report["canonical"] is None
    assert report["verdict"] == "REVIEW_REQUIRED"


def test_html_revision_parser_requires_explicit_metadata() -> None:
    missing: list[str] = []
    html = base64.b64encode(b"<html><body>historical plan</body></html>").decode()
    assert handover._public_revision({"content": html}, missing) is None
    assert missing


def test_repo_argument_does_not_accept_api_path_injection(tmp_path: Path) -> None:
    try:
        handover.collect(root=tmp_path, season=SEASON, repo="owner/repo/../../other", remote=False)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid repository selector accepted")


def test_last_issue_comment_is_context_not_new_authority(tmp_path: Path) -> None:
    runner = ReadOnlyRunner()
    runner.comments = 1
    report = handover.collect(root=_root(tmp_path), season=SEASON, runner=runner, issue=12)
    assert report["active_issue"]["latest_comment"]["excerpt"].startswith("Last verified SHA")
    assert report["active_issue"]["latest_comment"]["url"].endswith("issuecomment-1")
    assert report["verdict"] == "CONTEXT_VERIFIED"


def test_remote_branch_change_during_collection_requires_review(tmp_path: Path) -> None:
    runner = ReadOnlyRunner()
    runner.change_pages_midflight = True
    report = handover.collect(root=_root(tmp_path), season=SEASON, runner=runner)
    assert report["verdict"] == "REVIEW_REQUIRED"
    assert any("gh-pages changed during handover" in row for row in report["risks"])
