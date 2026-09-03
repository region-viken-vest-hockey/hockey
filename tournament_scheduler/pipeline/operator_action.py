"""Typed operator actions: machine-callable next steps for capability results.

``CapabilityResult.suggested_actions`` is human-readable prose — useful for a
person reading a terminal, but an autonomous operator (issue #11's
observe-decide-act loop) needs a stable identifier it can select and invoke
without interpreting text. :class:`OperatorAction` is that contract, and
:data:`DEFAULT_REGISTRY` maps the core action IDs below to real executors
built on top of the existing pipeline capabilities — nothing here duplicates
scheduling or recovery logic, it only gives it a stable calling convention.

Core actions registered by default:

- ``retry_source`` — re-attempt scraping one calendar source
- ``use_trusted_cache`` — accept a source's cached data instead of retrying
- ``refresh_source`` — force a cache-bypassing re-scrape of one source
- ``request_credentials`` — raise a credentials escalation question (#5)
- ``rerun_planning`` — rerun Stage 3 with the current config/source data
- ``compare_candidates`` — summarize Stage 3's recorded plan candidates (#4)
- ``export_selected_plan`` — run Stage 4 export for the current plan
- ``publish_pages`` — sanitize (#18), gate on a per-bundle approval (#19), publish
  the current Stage 4 export to GitHub Pages (#17), and verify it's reachable (#20)
- ``verify_pages`` — re-check that the last published Pages content is reachable (#20)
- ``rollback_pages`` — roll ``/latest/`` back to a previously published run (#20)

Risk levels and the approval rule: ``destructive`` (discards meaningful
data) and ``external`` (writes artifacts meant to leave the system, e.g.
exported season plans) actions always require approval — this is a safety
invariant enforced in ``OperatorAction.__post_init__``, not just a default,
matching the product direction's "external writes remain explicitly
authorized" principle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

from .run_manifest import is_durable as _manifest_is_durable

if TYPE_CHECKING:
    from .capability_result import CapabilityResult

# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

# Bump only on a breaking change to the OperatorAction shape. Additive
# fields (new optional keys with safe defaults) do not require a bump.
OPERATOR_ACTION_SCHEMA_VERSION = 1


class RiskLevel(str, Enum):
    """How much latitude an action has to run without a human in the loop."""

    SAFE = "safe"  # read-only or fully reversible; no approval needed
    REVERSIBLE = "reversible"  # changes local state, but rerunnable/undoable
    DESTRUCTIVE = "destructive"  # discards meaningful data
    EXTERNAL = "external"  # writes to, or publishes via, something outside the workspace


_VALID_RISK_LEVELS = {level.value for level in RiskLevel}
_APPROVAL_REQUIRED_RISK_LEVELS = {RiskLevel.DESTRUCTIVE.value, RiskLevel.EXTERNAL.value}


@dataclass
class OperatorAction:
    """A single machine-callable next step.

    Parameters
    ----------
    action_id:
        Stable identifier from the action registry (e.g. ``"retry_source"``).
    description:
        Human-readable explanation, for the same UIs that currently show
        ``CapabilityResult.suggested_actions`` strings.
    capability:
        Name of the capability this action operates on (e.g. ``"scraping"``).
    arguments:
        Concrete arguments to pass to the registered executor when this
        action is invoked (e.g. ``{"source_name": "Kongsberg ishall"}``).
    risk_level:
        One of ``safe``, ``reversible``, ``destructive``, ``external``.
    requires_approval:
        Whether a human must approve before this action runs. Forced to
        ``True`` for ``destructive``/``external`` risk levels — see module
        docstring.
    retryable:
        Whether this action may be safely retried after a failure.
    """

    action_id: str
    description: str
    capability: str
    arguments: dict[str, Any] = field(default_factory=dict)
    risk_level: str = RiskLevel.SAFE.value
    requires_approval: bool = False
    retryable: bool = True

    def __post_init__(self) -> None:
        risk_value = self.risk_level.value if isinstance(self.risk_level, RiskLevel) else str(self.risk_level)
        if risk_value not in _VALID_RISK_LEVELS:
            raise ValueError(
                f"Invalid risk level {risk_value!r}. Valid values: {', '.join(sorted(_VALID_RISK_LEVELS))}"
            )
        self.risk_level = risk_value
        if risk_value in _APPROVAL_REQUIRED_RISK_LEVELS and not self.requires_approval:
            raise ValueError(
                f"Action {self.action_id!r} has risk_level={risk_value!r} but requires_approval=False — "
                "destructive/external actions must require approval."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OPERATOR_ACTION_SCHEMA_VERSION,
            "action_id": self.action_id,
            "description": self.description,
            "capability": self.capability,
            "arguments": dict(self.arguments),
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "retryable": self.retryable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperatorAction":
        """Build an :class:`OperatorAction` from a dict, ignoring unknown keys."""
        return cls(
            action_id=data.get("action_id", ""),
            description=data.get("description", ""),
            capability=data.get("capability", ""),
            arguments=dict(data.get("arguments") or {}),
            risk_level=data.get("risk_level", RiskLevel.SAFE.value),
            requires_approval=bool(data.get("requires_approval", False)),
            retryable=bool(data.get("retryable", True)),
        )

    def with_arguments(self, **arguments: Any) -> "OperatorAction":
        """Return a copy of this action template with concrete arguments filled in."""
        return OperatorAction(
            action_id=self.action_id,
            description=self.description,
            capability=self.capability,
            arguments={**self.arguments, **arguments},
            risk_level=self.risk_level,
            requires_approval=self.requires_approval,
            retryable=self.retryable,
        )


# ---------------------------------------------------------------------------
# Structured errors
# ---------------------------------------------------------------------------


class ActionError(RuntimeError):
    """Base class for structured action-dispatch failures.

    Every failure mode the registry can produce has a stable ``code`` an
    agent can branch on without parsing the message text.
    """

    code = "action_error"

    def __init__(self, action_id: str, reason: str) -> None:
        self.action_id = action_id
        self.reason = reason
        super().__init__(f"{self.code}: {action_id}: {reason}")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "action_id": self.action_id, "reason": self.reason}


class UnknownActionError(ActionError):
    """Raised when an action_id is not in the registry."""

    code = "unknown_action"


class ApprovalRequiredError(ActionError):
    """Raised when an action requiring approval is invoked without it."""

    code = "approval_required"


class PersistenceUnavailableError(ActionError):
    """Raised when an approval-gated action would run without durable
    manifest persistence to record it (issue #14).

    A human's approval for a destructive/external action is itself an
    operator decision — if the manifest can't durably record what's about
    to happen (and, once it happens, what did happen), the action must not
    run at all, rather than executing untracked and hoping the record can
    be written after the fact.
    """

    code = "persistence_unavailable"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ActionExecutor = Callable[..., "CapabilityResult"]


@dataclass
class _RegisteredAction:
    template: OperatorAction
    executor: ActionExecutor


class ActionRegistry:
    """Maps stable action IDs to executable capabilities.

    Instances are independent (tests build their own small registries);
    :data:`DEFAULT_REGISTRY` is populated with the core actions this module
    ships.
    """

    def __init__(self) -> None:
        self._actions: dict[str, _RegisteredAction] = {}

    def register(self, template: OperatorAction, executor: ActionExecutor) -> None:
        self._actions[template.action_id] = _RegisteredAction(template=template, executor=executor)

    def known_action_ids(self) -> list[str]:
        return sorted(self._actions)

    def list_actions(self) -> list[OperatorAction]:
        """Return the registered action templates (empty ``arguments``)."""
        return [entry.template for entry in self._actions.values()]

    def _get(self, action_id: str) -> _RegisteredAction:
        entry = self._actions.get(action_id)
        if entry is None:
            raise UnknownActionError(action_id, f"No action registered with id {action_id!r}")
        return entry

    def build(self, action_id: str, **arguments: Any) -> OperatorAction:
        """Return a concrete :class:`OperatorAction` for *action_id* with *arguments* filled in.

        Raises :class:`UnknownActionError` for an unrecognized id.
        """
        return self._get(action_id).template.with_arguments(**arguments)

    def execute(self, action: OperatorAction, *, approved: bool = False) -> "CapabilityResult":
        """Run *action*'s registered executor with its arguments.

        Raises :class:`UnknownActionError` for an unrecognized id, or
        :class:`ApprovalRequiredError` when ``action.requires_approval`` is
        set and *approved* is not ``True``. For an approved, approval-gated
        action, also raises :class:`PersistenceUnavailableError` when the
        run manifest for its ``work_dir`` argument isn't durably writable
        (issue #14) — an approved destructive/external action must not run
        without a reliable way to record that it did. All three checks fail
        fast, before any executor code runs.
        """
        entry = self._get(action.action_id)
        if action.requires_approval and not approved:
            raise ApprovalRequiredError(
                action.action_id, "This action requires explicit human approval before it can run."
            )
        if action.requires_approval and approved:
            work_dir = action.arguments.get("work_dir")
            if work_dir and not _manifest_is_durable(work_dir):
                raise PersistenceUnavailableError(
                    action.action_id,
                    "Run manifest is not durably writable; refusing to execute an approved "
                    "action that would go unrecorded.",
                )
        return entry.executor(**action.arguments)


# ---------------------------------------------------------------------------
# Core action executors
# ---------------------------------------------------------------------------


def _find_source_config(cfg: dict[str, Any] | None, source_name: str) -> dict[str, Any] | None:
    for source in (cfg or {}).get("sources", []) or []:
        if source.get("name") == source_name:
            return source
    return None


def _write_cache_entry(work_dir: str, source_name: str, source_cfg: dict[str, Any], result: dict[str, Any]) -> int:
    from .cache_manager import ScrapedDataCache

    events = result.get("events", [])
    cache = ScrapedDataCache(work_dir=work_dir)
    data = cache.read()
    data.setdefault("sources", {})
    data["sources"][source_name] = {
        "name": source_name,
        "url": source_cfg.get("url", ""),
        "scrape_timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "event_count": len(events),
        "blocked": bool(result.get("blocked", False)),
        "events": events,
    }
    cache.write(data)
    return len(events)


def _execute_retry_source(*, work_dir: str, source_name: str) -> "CapabilityResult":
    from datetime import datetime as _dt

    from .capability_result import CapabilityResult
    from .stage1_config import load_effective_config
    from .stage2_scraping import _scrape_source
    from .state import PipelineState

    state = PipelineState(work_dir)
    cfg = load_effective_config(state)
    source_cfg = _find_source_config(cfg, source_name)
    if not cfg or source_cfg is None:
        return CapabilityResult.failed(f"Ukjent kilde: {source_name}", capability="source_health")

    start = _dt.strptime(cfg["start_date"], "%Y-%m-%d")
    end = _dt.strptime(cfg["end_date"], "%Y-%m-%d")
    result = _scrape_source(source_cfg, start_date=start, end_date=end)
    event_count = _write_cache_entry(work_dir, source_name, source_cfg, result)

    if result.get("blocked"):
        return CapabilityResult.blocked(
            f"{source_name}: fortsatt blokkert etter nytt forsøk",
            capability="source_health",
            problems=[str(result.get("block_reason") or "")],
        )
    return CapabilityResult.ok(
        f"{source_name}: {event_count} hendelser hentet på nytt", capability="source_health"
    )


def _execute_refresh_source(*, work_dir: str, source_name: str) -> "CapabilityResult":
    from .cache_manager import ScrapedDataCache

    ScrapedDataCache(work_dir=work_dir).force_refresh()
    return _execute_retry_source(work_dir=work_dir, source_name=source_name)


def _execute_use_trusted_cache(*, work_dir: str, source_name: str) -> "CapabilityResult":
    from .cache_manager import ScrapedDataCache
    from .capability_result import CapabilityResult

    cache = ScrapedDataCache(work_dir=work_dir)
    entry = (cache.read().get("sources") or {}).get(source_name)
    if entry is None:
        return CapabilityResult.failed(f"Ingen cache funnet for {source_name}", capability="source_health")
    return CapabilityResult.ok(
        f"{source_name}: bruker {entry.get('event_count', 0)} bufrede hendelser uten å skrape på nytt",
        capability="source_health",
        evidence=[f"cache_timestamp={entry.get('scrape_timestamp', '?')}"],
    )


def _execute_request_credentials(
    *, work_dir: str, source_name: str, env_vars: list[str] | None = None
) -> "CapabilityResult":
    from .capability_result import CapabilityResult
    from .escalation import Question, raise_question

    env_vars = env_vars or []
    recommendation = f"Sett miljøvariabler: {', '.join(env_vars)}" if env_vars else "Kontakt klubben for tilgang"
    question = Question(
        type="credentials",
        capability="source_health",
        summary=f"{source_name} trenger legitimasjon",
        context=f"Kilde {source_name} kan ikke skrapes uten legitimasjon.",
        alternatives=[recommendation],
        recommendation=recommendation,
        impact="Kilden forblir blokkert til legitimasjon er satt.",
    )
    raise_question(work_dir, question)
    return CapabilityResult.blocked(
        f"{source_name}: venter på legitimasjon",
        capability="source_health",
        problems=["Mangler legitimasjon"],
        suggested_actions=[recommendation],
    )


def _execute_rerun_planning(*, work_dir: str, iterations: int = 1) -> "CapabilityResult":
    from datetime import datetime as _dt

    from .capability_result import CapabilityResult
    from .stage1_config import load_effective_config
    from .stage3_planning import run as stage3_run
    from .state import PipelineState, StageName

    state = PipelineState(work_dir)
    cfg = load_effective_config(state)
    if not cfg:
        return CapabilityResult.failed("Ingen Stage 1-konfigurasjon funnet", capability="planning")
    scraping = state.read_stage(StageName.SCRAPING)
    start = _dt.strptime(cfg["start_date"], "%Y-%m-%d")
    end = _dt.strptime(cfg["end_date"], "%Y-%m-%d")
    try:
        result = stage3_run(cfg, scraping, state, start, end, iterations=iterations)
    except Exception as exc:  # noqa: BLE001 — surface as a structured failure, not a crash
        return CapabilityResult.failed(f"Planlegging feilet: {exc}", capability="planning")
    tournament_count = len((result.get("plan") or {}).get("tournaments", []))
    return CapabilityResult.ok(f"{tournament_count} turneringer planlagt på nytt", capability="planning")


def _execute_compare_candidates(*, work_dir: str) -> "CapabilityResult":
    from .capability_result import CapabilityResult
    from .state import PipelineState, StageName

    checkpoint = PipelineState(work_dir).read_stage(StageName.PLANNING) or {}
    candidates = checkpoint.get("candidates") or []
    if not candidates:
        return CapabilityResult.warning("Ingen kandidatdata funnet", capability="planning")
    selected = checkpoint.get("selected_candidate_attempt")
    return CapabilityResult.ok(
        f"{len(candidates)} kandidat(er) sammenlignet, forsøk {selected} valgt",
        capability="planning",
        evidence=[
            f"attempt={c.get('attempt')} status={c.get('status')} score={c.get('score')}" for c in candidates
        ],
    )


def _execute_export_selected_plan(*, work_dir: str, export_dir: str = "export") -> "CapabilityResult":
    from .capability_result import CapabilityResult
    from .stage4_export import run as stage4_run
    from .state import PipelineState, StageName

    state = PipelineState(work_dir)
    plan_checkpoint = state.read_stage(StageName.PLANNING)
    if not plan_checkpoint:
        return CapabilityResult.failed("Ingen Stage 3-plan funnet", capability="export")
    try:
        result = stage4_run(plan_checkpoint, state, export_dir=export_dir, strict=True)
    except Exception as exc:  # noqa: BLE001 — surface as a structured failure, not a crash
        return CapabilityResult.failed(f"Eksport feilet: {exc}", capability="export")
    files = result.get("output_files", {})
    return CapabilityResult.ok(
        f"{len(files)} fil(er) eksportert", capability="export", artifacts=list(files.values())
    )


# Answers that count as an explicit "yes, publish this" — anything else
# (a rejection, a question, an empty string) leaves the bundle unapproved.
# Deliberately an allowlist of exact tokens, not a substring/keyword search:
# a sentence like "not approved yet" must not be read as approval just
# because it contains the word "approved" (issue #19).
_PUBLICATION_APPROVAL_ANSWERS = {"godkjenn", "godkjent", "approve", "approved", "yes", "ja", "ok"}


def _is_publication_approved_answer(answer: str) -> bool:
    return answer.strip().lower() in _PUBLICATION_APPROVAL_ANSWERS


def _execute_publish_pages(
    *,
    work_dir: str,
    run_id: str | None = None,
    export_dir: str | None = None,
    repo_dir: str = ".",
    branch: str = "gh-pages",
    remote: str = "origin",
    push: bool = True,
    allowed_filenames: "frozenset[str] | set[str] | None" = None,
    allow_findings: "frozenset[str] | set[str] | None" = None,
    confirm_public: bool = False,
    dry_run: bool = False,
    verify: bool = True,
    verify_max_attempts: int | None = None,
    verify_retry_delay_seconds: float | None = None,
    fetch: "Callable[[str], Any] | None" = None,
    sleep: "Callable[[float], None] | None" = None,
) -> "CapabilityResult":
    """Publish the current export to GitHub Pages (issues #17/#18/#19/#20).

    Publishing a public URL is treated as a decision distinct from merely
    running this action at all: even when the caller already passed
    ``approved=True`` at the :class:`ActionRegistry` level (the coarse,
    structural "this is an external-risk action" gate from #10), pushing to
    *branch* additionally requires one of:

    - ``confirm_public=True`` — an explicit, same-invocation confirmation
      (the CLI's ``--confirm-public``), or
    - a previously *answered* ``external_publication`` escalation question
      whose id exactly matches this bundle's content and this exact
      target (repo/branch/remote/run_id) — see :func:`_is_publication_approved_answer`
      for what counts as an affirmative answer.

    Neither is present the first time a given bundle/target combination is
    seen: this raises (or finds already-pending) that question and returns
    a ``blocked``/``requires_human`` result *without publishing* — the
    question's id changes whenever the bundle content or target changes
    (see ``pages_publish.bundle_fingerprint``/``target_fingerprint``), so an
    old approval never silently covers new content, and a rejected answer
    stays rejected rather than being asked again. ``dry_run=True`` always
    takes this preview path, even when a matching approval already exists,
    and never publishes.

    After an actual push (``push=True`` and something changed), a push
    succeeding is not treated as the whole story (issue #20): unless
    ``verify=False``, ``pages_verify.verify_publication`` polls the
    published URL with bounded retries. If it can't confirm the expected
    content within that window, the final result is downgraded from ``ok``
    to ``warning`` — "pushed, but not yet confirmed reachable" is reported
    explicitly rather than as an unqualified success. Verification is
    always skipped when ``push=False`` — there is no public URL to check
    against local-only commits.
    """
    from pathlib import Path

    from . import pages_bundle, pages_publish, pages_verify
    from .capability_result import CapabilityResult
    from .escalation import EscalationType, Question, raise_question
    from .run_manifest import RunManifest
    from .state import PipelineState, StageName

    state = PipelineState(work_dir)
    export_checkpoint = state.read_stage(StageName.EXPORT) or {}
    output_files = export_checkpoint.get("output_files") or {}

    if export_dir is None:
        html_path = output_files.get("html")
        if not html_path:
            return CapabilityResult.failed(
                "Ingen Stage 4-eksport funnet — kjør eksport før publisering.", capability="pages_publish"
            )
        export_dir = str(Path(html_path).parent)

    if run_id is None:
        run_id = RunManifest(work_dir).read().get("run_id") or "unknown-run"

    planning_checkpoint = state.read_stage(StageName.PLANNING) or {}
    plan_dict = planning_checkpoint.get("plan") if isinstance(planning_checkpoint, dict) else None
    hard_collisions = []
    manual_host_count = 0
    if isinstance(plan_dict, dict):
        hard_collisions = list(plan_dict.get("arena_day_collisions") or [])
        for tournament in plan_dict.get("tournaments") or []:
            if isinstance(tournament, dict) and tournament.get("manual_booking_reason"):
                manual_host_count += 1

    collision_warning: str | None = None
    if hard_collisions or manual_host_count:
        parts: list[str] = []
        if hard_collisions:
            parts.append(f"{len(hard_collisions)} arena-/dagskollisjon(er)")
        if manual_host_count:
            parts.append(f"{manual_host_count} turnering(er) hos klubb uten skrapet kalender")
        collision_warning = (
            f"{' og '.join(parts)} krever manuell istidsplanlegging og ligger i "
            "'Må planlegges manuelt'-visningen (manual_schedule.html). De er en del av "
            "planen, men må bookes manuelt av vertsklubben før de er endelige."
        )

    def _with_collision_warning(result: "CapabilityResult") -> "CapabilityResult":
        """Attach collision/manual-booking context without hard-blocking."""
        if collision_warning:
            result.problems = [collision_warning] + list(result.problems)
            result.evidence = list(result.evidence) + [
                f"arena_day_collisions={len(hard_collisions)}",
                f"manual_booking_hosts={manual_host_count}",
            ]
            if result.status == "ok":
                result.summary = f"{result.summary} Merk: {collision_warning}"
                result.status = "warning"
        return result

    # A raw Stage 4 export may contain rosters, contact info, or internal
    # notes (Spond exports, review_packets/) that must never reach a
    # public URL — sanitize into a separate bundle first (issue #18) and
    # publish that instead of the raw export directory. A blocked/failed
    # sanitization result is returned as-is; the git publish step never
    # runs on unsanitized content.
    public_bundle_dir = str(Path(work_dir) / "public_bundle")
    bundle_result = pages_bundle.build_public_bundle(
        export_dir,
        public_bundle_dir,
        allowed_filenames=allowed_filenames,
        allow_findings=allow_findings,
    )
    if not bundle_result.is_terminal_success:
        return bundle_result

    bundle_fp = pages_publish.bundle_fingerprint(public_bundle_dir)
    target_fp = pages_publish.target_fingerprint(
        repo_dir=repo_dir, branch=branch, remote=remote, run_id=run_id
    )
    question = Question(
        type=EscalationType.EXTERNAL_PUBLICATION.value,
        capability="pages_publish",
        summary=(
            f"Godkjenn offentlig publisering av kjøring {run_id} til '{branch}' "
            f"(bunt {bundle_fp[:12]}, mål {target_fp[:12]})?"
        ),
    )

    def _raise_preview_question() -> dict[str, list[str]]:
        diff = pages_publish.diff_latest(public_bundle_dir, repo_dir=repo_dir, branch=branch)
        urls = pages_publish.resolve_urls(repo_dir=repo_dir, remote=remote, run_id=run_id)
        question.context = (
            f"{bundle_result.summary} Endringer under /latest/: "
            f"+{len(diff['add'])} ~{len(diff['update'])} -{len(diff['remove'])}."
        )
        question.alternatives = [
            "Kjør 'rvv-miniputt operator publish --confirm-public' for å publisere denne bunten med en gang",
            f"Svar 'godkjenn' på spørsmål {question.id} for en varig godkjenning",
        ]
        question.recommendation = "Se over filene i personvernrapporten og endringslisten før godkjenning."
        if urls:
            question.impact = f"Publiserer offentlig til {urls[0]} og {urls[1]}."
        raise_question(work_dir, question)
        return diff

    if dry_run:
        diff = _raise_preview_question()
        return _with_collision_warning(CapabilityResult.ok(
            f"Forhåndsvisning av publisering til '{branch}' — ingen publisering utført "
            f"(+{len(diff['add'])} ~{len(diff['update'])} -{len(diff['remove'])} under /latest/).",
            capability="pages_publish",
            evidence=[
                f"bundle_fingerprint={bundle_fp}",
                f"target_fingerprint={target_fp}",
                f"question_id={question.id}",
            ],
            artifacts=list(bundle_result.artifacts),
        ))

    approved_now = confirm_public
    if not approved_now:
        existing = next(
            (q for q in RunManifest(work_dir).all_questions() if q.get("id") == question.id), None
        )
        if existing is not None and existing.get("answered"):
            if _is_publication_approved_answer(existing.get("answer") or ""):
                approved_now = True
            else:
                return _with_collision_warning(CapabilityResult.blocked(
                    f"Publisering av denne bunten ble avvist tidligere (svar: {existing.get('answer')!r}).",
                    capability="pages_publish",
                    problems=["Godkjenning avvist for denne bunt-/mål-kombinasjonen."],
                    evidence=[f"bundle_fingerprint={bundle_fp}", f"target_fingerprint={target_fp}"],
                    artifacts=list(bundle_result.artifacts),
                ))
        else:
            diff = _raise_preview_question()
            return _with_collision_warning(CapabilityResult.blocked(
                f"Publisering krever eksplisitt godkjenning for denne bunten (bunt {bundle_fp[:12]}, "
                f"mål {target_fp[:12]}).",
                capability="pages_publish",
                suggested_actions=list(question.alternatives),
                evidence=[
                    f"bundle_fingerprint={bundle_fp}",
                    f"target_fingerprint={target_fp}",
                    f"question_id={question.id}",
                    f"diff_add={len(diff['add'])}",
                    f"diff_update={len(diff['update'])}",
                    f"diff_remove={len(diff['remove'])}",
                ],
                artifacts=list(bundle_result.artifacts),
            ))

    publish_result = pages_publish.publish(
        export_dir=public_bundle_dir,
        run_id=run_id,
        repo_dir=repo_dir,
        branch=branch,
        remote=remote,
        push=push,
        bundle_fingerprint=bundle_fp,
    )
    publish_result.evidence = (
        list(bundle_result.evidence)
        + [f"bundle_fingerprint={bundle_fp}", f"target_fingerprint={target_fp}", f"run_id={run_id}"]
        + list(publish_result.evidence)
    )
    publish_result.artifacts = list(publish_result.artifacts) + list(bundle_result.artifacts)

    if push and verify and publish_result.status == "ok":
        urls = [a for a in publish_result.artifacts if isinstance(a, str) and a.startswith("http")]
        latest_url = urls[0] if urls else None
        run_url = urls[1] if len(urls) > 1 else None
        if latest_url is not None:
            verify_kwargs: dict[str, Any] = {}
            if verify_max_attempts is not None:
                verify_kwargs["max_attempts"] = verify_max_attempts
            if verify_retry_delay_seconds is not None:
                verify_kwargs["retry_delay_seconds"] = verify_retry_delay_seconds
            if fetch is not None:
                verify_kwargs["fetch"] = fetch
            if sleep is not None:
                verify_kwargs["sleep"] = sleep
            verify_result = pages_verify.verify_publication(
                latest_url=latest_url,
                run_url=run_url,
                bundle_fingerprint=bundle_fp,
                run_id=run_id,
                **verify_kwargs,
            )
            publish_result.evidence = list(publish_result.evidence) + [f"verify_status={verify_result.status}"] + list(
                verify_result.evidence
            )
            if verify_result.status != "ok":
                publish_result.status = verify_result.status
                publish_result.summary = f"{publish_result.summary} {verify_result.summary}"
                publish_result.problems = list(publish_result.problems) + list(verify_result.problems)
                publish_result.suggested_actions = list(publish_result.suggested_actions) + list(
                    verify_result.suggested_actions
                )

    return _with_collision_warning(publish_result)


def _last_ok_pages_publish_capability(work_dir: str) -> dict[str, Any] | None:
    from .run_manifest import RunManifest

    manifest = RunManifest(work_dir).read()
    for entry in reversed(manifest.get("capabilities") or []):
        if entry.get("capability") == "pages_publish" and entry.get("status") in ("ok", "warning"):
            return entry
    return None


def _evidence_value(entry: dict[str, Any], key: str) -> str | None:
    prefix = f"{key}="
    for item in entry.get("evidence") or []:
        if isinstance(item, str) and item.startswith(prefix):
            return item[len(prefix):]
    return None


def _execute_verify_pages(
    *,
    work_dir: str,
    latest_url: str | None = None,
    run_url: str | None = None,
    bundle_fingerprint: str | None = None,
    run_id: str | None = None,
    max_attempts: int | None = None,
    retry_delay_seconds: float | None = None,
    fetch: "Callable[[str], Any] | None" = None,
    sleep: "Callable[[float], None] | None" = None,
) -> "CapabilityResult":
    """Verify the last published GitHub Pages content is reachable (issue #20).

    Resolves *latest_url*/*run_url*/*bundle_fingerprint*/*run_id* from the
    run manifest's last recorded ``pages_publish`` capability result when
    not given explicitly, so ``rvv-miniputt operator verify`` with no
    arguments re-checks whatever was published most recently.
    """
    from . import pages_verify
    from .capability_result import CapabilityResult

    if latest_url is None or bundle_fingerprint is None or run_id is None:
        last = _last_ok_pages_publish_capability(work_dir)
        if last is None:
            return CapabilityResult.failed(
                "Ingen tidligere Pages-publisering funnet å verifisere.", capability="pages_verify"
            )
        urls = [a for a in (last.get("artifacts") or []) if isinstance(a, str) and a.startswith("http")]
        latest_url = latest_url or (urls[0] if urls else None)
        run_url = run_url or (urls[1] if len(urls) > 1 else None)
        bundle_fingerprint = bundle_fingerprint or _evidence_value(last, "bundle_fingerprint")
        run_id = run_id or _evidence_value(last, "run_id")
        if not latest_url or not bundle_fingerprint or not run_id:
            return CapabilityResult.failed(
                "Kunne ikke utlede URL, fingeravtrykk eller run_id fra siste Pages-publisering.",
                capability="pages_verify",
            )

    verify_kwargs: dict[str, Any] = {}
    if max_attempts is not None:
        verify_kwargs["max_attempts"] = max_attempts
    if retry_delay_seconds is not None:
        verify_kwargs["retry_delay_seconds"] = retry_delay_seconds
    if fetch is not None:
        verify_kwargs["fetch"] = fetch
    if sleep is not None:
        verify_kwargs["sleep"] = sleep

    return pages_verify.verify_publication(
        latest_url=latest_url,
        run_url=run_url,
        bundle_fingerprint=bundle_fingerprint,
        run_id=run_id,
        **verify_kwargs,
    )


def _execute_rollback_pages(
    *,
    work_dir: str,
    run_id: str,
    repo_dir: str = ".",
    branch: str = "gh-pages",
    remote: str = "origin",
    push: bool = True,
    confirm_public: bool = False,
) -> "CapabilityResult":
    """Roll ``/latest/`` back to a previously published run (issue #20).

    Gated the same way as ``publish_pages`` (issue #19): requires either
    ``confirm_public=True`` on this exact invocation or a previously
    answered ``external_publication`` question for this exact rollback
    target (branch/remote/run_id) — running this action at all
    (``approved=True`` at the registry level) is not sufficient on its own.
    """
    from . import pages_publish
    from .capability_result import CapabilityResult
    from .escalation import EscalationType, Question, raise_question
    from .run_manifest import RunManifest

    target_fp = pages_publish.target_fingerprint(repo_dir=repo_dir, branch=branch, remote=remote, run_id=run_id)
    question = Question(
        type=EscalationType.EXTERNAL_PUBLICATION.value,
        capability="pages_publish",
        summary=(
            f"Godkjenn tilbakerulling av '/latest/' til kjøring {run_id} på '{branch}' (mål {target_fp[:12]})?"
        ),
    )

    approved_now = confirm_public
    if not approved_now:
        existing = next(
            (q for q in RunManifest(work_dir).all_questions() if q.get("id") == question.id), None
        )
        if existing is not None and existing.get("answered"):
            if _is_publication_approved_answer(existing.get("answer") or ""):
                approved_now = True
            else:
                return CapabilityResult.blocked(
                    f"Tilbakerulling til kjøring {run_id} ble avvist tidligere (svar: {existing.get('answer')!r}).",
                    capability="pages_publish",
                    problems=["Godkjenning avvist for denne tilbakerullingen."],
                    evidence=[f"target_fingerprint={target_fp}"],
                )
        else:
            question.context = (
                f"Ruller '/latest/' på '{branch}' tilbake til den tidligere publiserte kjøringen {run_id} "
                "— historiske kjøringer under /runs/ berøres ikke."
            )
            question.alternatives = [
                "Kjør 'rvv-miniputt operator rollback --confirm-public' for å rulle tilbake med en gang",
                f"Svar 'godkjenn' på spørsmål {question.id} for en varig godkjenning",
            ]
            question.recommendation = "Bekreft at dette er riktig kjøring å rulle 'latest' tilbake til."
            raise_question(work_dir, question)
            return CapabilityResult.blocked(
                f"Tilbakerulling krever eksplisitt godkjenning (mål {target_fp[:12]}).",
                capability="pages_publish",
                suggested_actions=list(question.alternatives),
                evidence=[f"target_fingerprint={target_fp}", f"question_id={question.id}"],
            )

    return pages_publish.rollback_to_run(run_id=run_id, repo_dir=repo_dir, branch=branch, remote=remote, push=push)


def build_default_registry() -> ActionRegistry:
    """Build the registry of core operator actions this module ships."""
    registry = ActionRegistry()

    registry.register(
        OperatorAction(
            action_id="retry_source",
            description="Re-attempt scraping one calendar source.",
            capability="source_health",
            risk_level=RiskLevel.REVERSIBLE.value,
        ),
        _execute_retry_source,
    )
    registry.register(
        OperatorAction(
            action_id="refresh_source",
            description="Force a cache-bypassing re-scrape of one calendar source.",
            capability="source_health",
            risk_level=RiskLevel.REVERSIBLE.value,
        ),
        _execute_refresh_source,
    )
    registry.register(
        OperatorAction(
            action_id="use_trusted_cache",
            description="Accept a source's cached data instead of retrying it.",
            capability="source_health",
            risk_level=RiskLevel.SAFE.value,
        ),
        _execute_use_trusted_cache,
    )
    registry.register(
        OperatorAction(
            action_id="request_credentials",
            description="Raise a credentials escalation question for a blocked source.",
            capability="source_health",
            risk_level=RiskLevel.SAFE.value,
        ),
        _execute_request_credentials,
    )
    registry.register(
        OperatorAction(
            action_id="rerun_planning",
            description="Rerun Stage 3 planning with the current config and source data.",
            capability="planning",
            risk_level=RiskLevel.REVERSIBLE.value,
        ),
        _execute_rerun_planning,
    )
    registry.register(
        OperatorAction(
            action_id="compare_candidates",
            description="Summarize Stage 3's recorded plan candidates and which was selected.",
            capability="planning",
            risk_level=RiskLevel.SAFE.value,
        ),
        _execute_compare_candidates,
    )
    registry.register(
        OperatorAction(
            action_id="export_selected_plan",
            description="Run Stage 4 export for the currently selected plan.",
            capability="export",
            risk_level=RiskLevel.EXTERNAL.value,
            requires_approval=True,
        ),
        _execute_export_selected_plan,
    )
    registry.register(
        OperatorAction(
            action_id="publish_pages",
            description="Publish the current Stage 4 export to GitHub Pages (gh-pages branch).",
            capability="pages_publish",
            risk_level=RiskLevel.EXTERNAL.value,
            requires_approval=True,
        ),
        _execute_publish_pages,
    )
    registry.register(
        OperatorAction(
            action_id="verify_pages",
            description="Verify that the last published GitHub Pages content is reachable.",
            capability="pages_verify",
            risk_level=RiskLevel.SAFE.value,
        ),
        _execute_verify_pages,
    )
    registry.register(
        OperatorAction(
            action_id="rollback_pages",
            description="Roll '/latest/' back to a previously published run on GitHub Pages.",
            capability="pages_publish",
            risk_level=RiskLevel.EXTERNAL.value,
            requires_approval=True,
        ),
        _execute_rollback_pages,
    )

    return registry


DEFAULT_REGISTRY = build_default_registry()
