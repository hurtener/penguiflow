#!/usr/bin/env python
"""Import agent traces from the platform backend and score them with a metric.

IMPORTANT — two places a trace can live, selected by --source:
    * trajectories: the first-class `/state-store/trajectories?session_id[eq]=` table.
      Populated only for agents wired to a PERSISTENT StateStore. Each item is
      {trace_id, session_id, data:{...serialise()...}, ...} and `data` is exactly
      the trajectory shape the metric helpers read.
    * messages: the fallback for the HISTORICAL backlog, where the planner ran an
      in-memory StateStore and the trajectory was never persisted — but a copy
      rode along inside each ASSISTANT message's `metadata` (steps[].action...),
      fetched via `/sessions/{id}/messages`. One eval row per assistant turn.
    * auto (default): try trajectories first; fall back to messages per session,
      so a mixed old+new dataset imports correctly in a single run.

No agent re-run, no direct database access — read-only over the platform-backend
HTTP API.

Rows are built by the library, not by hand: each PB payload is rehydrated with
`Trajectory.from_serialised` (PB stores exactly `trajectory.serialise()`) and fed
to `penguiflow.evals.export.collect_trace_rows` through a tiny in-memory store
shim, so the emitted dataset.jsonl + manifest.json are the same TraceExampleV1
bundle `export_dataset` produces — metrics are portable in both directions.
Session/agent/user enumeration stays here: no PB endpoint lists traces globally
(`/state-store/trajectories` requires `session_id[eq]`), so `TraceSelector` has
nothing to select over for imported production traces.

Auth: a PB app deployed with Databricks "User Authorization" (OBO). A workspace PAT
(`dapi...`) is REJECTED by the app's OAuth proxy, but two things DO work:

  (preferred) A Databricks OAuth user-to-machine (U2M) token — a real user-identity
  OAuth token, which the OBO proxy accepts as `Authorization: Bearer`. The CLI mints
  and auto-refreshes it, so no manual copy/paste and no expiry dance:
    1. databricks auth login --host https://<your-workspace>.cloud.databricks.com
    2. run this script with --databricks-profile <profile>, or set
       PB_DATABRICKS_PROFILE (default: DEFAULT). If several CLI profiles match
       the same host, always name one explicitly.
       (the script shells out to `databricks auth token` each run)

The platform backend URLs in PB_ENVS are placeholders — substitute your own, or
pass --pb-url / set PB_URL_<ENV> (e.g. PB_URL_DEV).

  (fallback) The browser session cookie `__Host-databricksapps`:
    1. Visit the PB app URL in a browser and complete login.
    2. DevTools -> Application -> Cookies -> copy the value of `__Host-databricksapps`.
    3. export PB_COOKIE='<value>'      (the cookie expires — re-grab if it lapses)

Usage:
    # See your sessions (id | #msgs | title):
    uv run python scripts/run_import_eval.py --list-sessions

    # Import + score specific sessions:
    uv run python scripts/run_import_eval.py \
        --session-id <user-id>_session_<timestamp> \
        --session-id <user-id>_session_<timestamp> \
        --out /tmp/imported_eval

    # Import + score ALL your sessions with your own metric:
    uv run python scripts/run_import_eval.py --all-sessions \
        --metric-spec my_pkg.metrics:my_metric --metric-project-root /path/to/agent

    # Download traces for one agent across MANY users. What you can actually read
    # is whatever your credential is authorized for, so check with your platform
    # operators before running a cross-user import. Cap the volume with
    # --limit-traces:
    uv run python scripts/run_import_eval.py --agent-id <agent-id> \
        --user-id alice@corp.com --user-id bob@corp.com \
        --users-file emails.txt --session-limit 0 --limit-traces 500 \
        --out /tmp/imported_eval_all_users
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from penguiflow.evals import resolve_callable, run_harness_eval, wrap_metric
from penguiflow.evals.export import collect_trace_rows
from penguiflow.evals.helpers import extract_node_sequence
from penguiflow.planner import Trajectory

# Platform backend base URLs, one per environment. Override any of them at runtime
# with `--pb-url` or the per-env env vars PB_URL_DEV / PB_URL_RC1 / PB_URL_PROD /
# PB_URL_STAGING — useful for a one-off host without editing this file.
# NOTE: these are internal deployment hosts. Replace them with `<placeholder>`
# values before publishing this script anywhere public; `_resolve_pb_url` then
# refuses to run rather than firing requests at a bogus host.
PB_ENVS = {
    "dev": "https://dev-ai-platform-backend-185296477739056.aws.databricksapps.com",
    "rc1": "https://rc1-ai-platform-backend-185296477739056.aws.databricksapps.com",
    "prod": "https://prd-ai-platform-backend-185296477739056.aws.databricksapps.com",  # note: prefix 'prd'
    "staging": "https://staging-ai-platform-backend-185296477739056.aws.databricksapps.com",
}
PB_URL_PLACEHOLDER = "<"  # any surviving angle bracket means "not configured yet"


class AuthWallError(RuntimeError):
    """PB returned an OAuth login page instead of JSON (cookie missing/expired)."""


async def _get_json(client: httpx.AsyncClient, headers: dict[str, str], path: str, params: dict | None = None) -> Any:
    resp = await client.get(path, params=params, headers=headers)
    if resp.status_code in (301, 302, 303, 307, 308) or "text/html" in resp.headers.get("content-type", ""):
        raise AuthWallError(
            "Platform backend returned an OAuth login page, not JSON — your "
            "credential is missing, wrong, or expired. For OAuth re-run "
            "`databricks auth login`; for the cookie, re-grab __Host-databricksapps."
        )
    resp.raise_for_status()
    return resp.json()


async def list_sessions(client: httpx.AsyncClient, headers: dict[str, str], limit: int) -> list[dict[str, Any]]:
    data = await _get_json(client, headers, "/sessions", {"limit": limit})
    return list(data.get("sessions", []))


async def fetch_messages(client: httpx.AsyncClient, headers: dict[str, str], session_id: str) -> list[dict[str, Any]]:
    data = await _get_json(client, headers, f"/sessions/{session_id}/messages")
    return list(data.get("messages", []))


async def _with_retry(fn, client, headers, session_id, attempts: int = 3):
    """Retry a per-session fetch on transient timeouts/network errors (not auth)."""
    for attempt in range(1, attempts + 1):
        try:
            return await fn(client, headers, session_id)
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt == attempts:
                raise
            await asyncio.sleep(0.5 * attempt)


async def fetch_trajectories(
    client: httpx.AsyncClient, headers: dict[str, str], session_id: str
) -> list[dict[str, Any]]:
    """First-class stored traces for a session, or [] if the agent never persisted."""
    data = await _get_json(
        client, headers, "/state-store/trajectories", {"session_id[eq]": session_id, "limit": 200}
    )
    if isinstance(data, list):
        return data
    return list(data.get("items", []))  # tolerate an envelope if the API adds one


async def _derive_user_id(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    """The /sessions rows carry the caller's user_id; use it to build agent URLs."""
    data = await _get_json(client, headers, "/sessions", {"limit": 1})
    sessions = data.get("sessions", [])
    if not sessions:
        raise SystemExit("Could not derive user_id from /sessions (no sessions) — pass --user-id.")
    return sessions[0]["user_id"]


async def list_agent_sessions(
    client: httpx.AsyncClient, headers: dict[str, str], user_id: str, agent_id: str, limit: int
) -> list[dict[str, Any]]:
    """All sessions for ONE (user, agent), via the paginated v2 endpoint.

    This is the real per-agent scoping: plain /sessions is user-scoped and carries
    no agent id, but /v2/users/{user_id}/agents/{agent_id} returns only this agent's
    sessions ({session_id, session_title, timestamp, ...}). limit<=0 means all pages."""
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        data = await _get_json(client, headers, f"/v2/users/{user_id}/agents/{agent_id}", params)
        out.extend(data.get("sessions", []))
        if limit and len(out) >= limit:
            return out[:limit]
        if not data.get("has_more"):
            return out
        cursor = data.get("next_cursor")
        if not cursor:
            return out


@dataclass(slots=True)
class _TraceEntry:
    """One rehydrated trace, ready for the library row builder."""

    trace_id: str
    session_id: str
    source: str
    trajectory: Trajectory
    answer: str
    raw: dict[str, Any]


class _PrefetchedStore:
    """Minimal StateStore surface serving traces already downloaded over HTTP.

    Why: `collect_trace_rows` builds canonical TraceExampleV1 rows but pulls each
    trace from a store, while the PB bulk endpoint already returned every
    trajectory for a session in a single response. Replaying those from memory
    keeps the library's row builder without re-fetching a thing.

    Only the two methods `collect_trace_rows` actually calls are implemented;
    `list_planner_events` is deliberately absent so the hasattr guard skips it.
    """

    def __init__(self, entries: list[_TraceEntry]) -> None:
        self._by_ref = {(entry.trace_id, entry.session_id): entry.trajectory for entry in entries}

    async def get_trajectory(self, trace_id: str, session_id: str) -> Trajectory | None:
        return self._by_ref.get((trace_id, session_id))

    async def load_history(self, trace_id: str) -> list[Any]:
        return []  # PB flow events are not fetched; events.flow_events stays empty


def _rehydrate(payload: dict[str, Any], ref: str) -> Trajectory | None:
    """Rebuild a Trajectory from PB's stored `data` (which IS serialise() output).

    Malformed or partial payloads (common in the historical messages backlog) are
    reported and skipped rather than aborting the whole import."""
    try:
        return Trajectory.from_serialised(payload)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"      ! unrehydratable trace {ref}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def _trajectory_answer(traj: dict[str, Any]) -> str:
    """Best-effort final answer from a serialised trajectory (field name varies).

    Still needed because `_build_trace_example` hardcodes `outputs.final = None` —
    the library cannot know the answer, but the PB payload carries it."""
    for key in ("final_answer", "answer", "final_response", "response"):
        val = traj.get(key)
        if isinstance(val, str) and val:
            return val
    steps = traj.get("steps") or []
    if steps and isinstance(steps[-1], dict):
        for key in ("answer", "final_answer"):
            val = steps[-1].get(key)
            if isinstance(val, str) and val:
                return val
    return ""


def entries_from_messages(session_id: str, messages: list[dict[str, Any]]) -> list[_TraceEntry]:
    """One entry per assistant turn: pair its trajectory metadata with the
    preceding user query. Assistant turns without a step trajectory are skipped."""
    entries: list[_TraceEntry] = []
    last_user = ""
    for msg in messages:
        mtype = msg.get("message_type")
        if mtype == "user":
            last_user = str(msg.get("content") or "")
        elif mtype == "assistant":
            metadata = msg.get("metadata") or {}
            if not extract_node_sequence(metadata):
                continue  # no planner steps -> nothing to score
            # Historical metadata is a partial trajectory that often omits the
            # query; the preceding user turn is the query.
            payload = dict(metadata)
            if not payload.get("query"):
                payload["query"] = last_user
            trace_id = f"msg{msg.get('id')}"
            trajectory = _rehydrate(payload, f"{session_id}#{trace_id}")
            if trajectory is None:
                continue
            entries.append(
                _TraceEntry(
                    trace_id=trace_id,
                    session_id=session_id,
                    source="messages",
                    trajectory=trajectory,
                    answer=str(msg.get("content") or ""),
                    raw=dict(metadata),
                )
            )
    return entries


def entries_from_trajectories(session_id: str, trajectories: list[dict[str, Any]]) -> list[_TraceEntry]:
    """One entry per stored trajectory. Each item's `data` IS trajectory.serialise();
    items without a node sequence are skipped (nothing to score)."""
    entries: list[_TraceEntry] = []
    for item in trajectories:
        data = item.get("data") or {}
        if not extract_node_sequence(data):
            continue
        trace_id = str(item.get("trace_id") or "")
        trajectory = _rehydrate(data, f"{session_id}#{trace_id}")
        if trajectory is None:
            continue
        entries.append(
            _TraceEntry(
                trace_id=trace_id,
                session_id=session_id,
                source="trajectories",
                trajectory=trajectory,
                answer=_trajectory_answer(data),
                raw=data,
            )
        )
    return entries


async def build_dataset_rows(
    entries: list[_TraceEntry], default_split: str, workload: str | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Turn rehydrated traces into canonical TraceExampleV1 dataset rows.

    The row shape, redaction block and manifest all come from the library, so an
    imported dataset is interchangeable with one produced by `export_dataset`."""
    refs = [{"trace_id": entry.trace_id, "session_id": entry.session_id} for entry in entries]
    collected = await collect_trace_rows(
        state_store=_PrefetchedStore(entries),
        trace_ids=[ref["trace_id"] for ref in refs],
        trace_refs=refs,
        workload=workload,
    )

    rows: list[dict[str, Any]] = []
    for entry, trace_row in zip(entries, collected["rows"], strict=True):
        trace_row["outputs"]["final"] = entry.answer
        # Trajectory.serialise() has a fixed key set, so a from_serialised round
        # trip silently drops anything outside it (e.g. the top-level `reason`
        # that historical message metadata carries). Metrics read pred_trace, so
        # hand back the untouched payload rather than the re-serialised one.
        trace_row["trajectory_full"] = entry.raw
        # Production traces carry no `split:` tag, so fall back to the CLI split.
        tagged_split = str(trace_row.get("trajectory", {}).get("split") or "unknown")
        split = tagged_split if tagged_split != "unknown" else default_split
        trace_row["trajectory"]["split"] = split
        rows.append(
            {
                "example_id": f"{entry.session_id}#{entry.trace_id}",
                "split": split,
                "source": entry.source,
                "question": str(trace_row.get("query") or ""),
                "gold_trace": trace_row,
            }
        )
    collected["manifest"]["counts"] = {
        "total": len(rows),
        **{split: sum(1 for row in rows if row["split"] == split) for split in {row["split"] for row in rows}},
    }
    return rows, collected["manifest"]


def _make_run_one():
    """run_one that scores the stored trajectory (no re-run).

    run_harness_eval unpacks (pred, pred_trace) and calls
    metric(gold, pred, gold, pred_name, pred_trace). pred_trace is the serialised
    trajectory, whose `steps[].action.next_node` is what metric helpers read.
    """

    def run_one(gold: dict[str, Any], patch_bundle: dict[str, Any] | None = None):
        row = gold.get("gold_trace") or {}
        outputs = row.get("outputs") or {}
        return str(outputs.get("final") or ""), (row.get("trajectory_full") or {})

    return run_one


def smoke_metric(gold, pred, trace=None, pred_name=None, pred_trace=None) -> dict[str, Any]:
    """Default agent-agnostic metric so the pipeline yields output for any agent.

    Scores 1.0 when the trajectory completed with a non-empty node sequence.
    Replace with your own metric via --metric-spec for real evaluation."""
    nodes = extract_node_sequence(pred_trace)
    reason = pred_trace.get("reason") if isinstance(pred_trace, dict) else None
    completed = reason == "answer_complete"
    score = 1.0 if (nodes and completed) else (0.5 if nodes else 0.0)
    return {"score": score, "feedback": f"nodes={nodes}; reason={reason}"}


def _parse_checks(feedback: str | None) -> dict[str, str] | None:
    """Parse a strict 'k=0|k=1|...' feedback string into {check: '0'/'1'}.

    Returns None unless EVERY token is `<name>=<0 or 1>`, so free-form feedback
    (e.g. the smoke metric's 'nodes=[...]; reason=...') is left as-is."""
    if not feedback or "=" not in feedback:
        return None
    checks: dict[str, str] = {}
    for tok in feedback.split("|"):
        key, sep, val = tok.partition("=")
        if not sep or not key.replace("_", "").isalpha() or val not in ("0", "1"):
            return None
        checks[key] = val
    return checks or None


def _write_csv(csv_path: Path, rows: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    """Join per-row trace data with per-row scores into a flat CSV. When a metric
    encodes per-criterion checks in `feedback` (k=0/1|...), expand them into one
    column per criterion (the harness drops the raw `checks` dict)."""
    by_id = {r["example_id"]: r for r in rows}

    # Discover the union of check columns, ordered by first appearance.
    parsed: dict[str, dict[str, str] | None] = {}
    check_keys: list[str] = []
    for res in results:
        checks = _parse_checks(res.get("feedback"))
        parsed[res["example_id"]] = checks
        for key in checks or {}:
            if key not in check_keys:
                check_keys.append(key)

    base_header = [
        "example_id", "session_id", "trace_ref", "source", "status", "score", "split",
        "nodes", "num_steps", "question", "answer_preview", "feedback",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(base_header + check_keys)
        for res in results:
            eid = res["example_id"]
            row = by_id.get(eid) or {}
            gold = row.get("gold_trace") or {}
            traj = gold.get("trajectory_full") or {}
            session_id, _, trace_ref = eid.partition("#")
            checks = parsed.get(eid) or {}
            writer.writerow(
                [
                    eid, session_id, trace_ref, row.get("source", ""),
                    (gold.get("outputs") or {}).get("status", ""),
                    res.get("score"), res.get("split"),
                    "|".join(extract_node_sequence(traj)),
                    (gold.get("trajectory") or {}).get("steps", 0),
                    (gold.get("query") or "")[:100].replace("\n", " "),
                    str((gold.get("outputs") or {}).get("final") or "")[:120].replace("\n", " "),
                    res.get("feedback", ""),
                ]
                + [checks.get(key, "") for key in check_keys]
            )


def _oauth_token_from_cli(profile: str, host: str | None) -> str:
    """Mint/refresh a Databricks OAuth U2M token via the CLI (accepted as Bearer)."""
    cmd = ["databricks", "auth", "token", "--profile", profile]
    if host:
        cmd += ["--host", host]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise SystemExit("`databricks` CLI not found on PATH — install it or use cookie auth.") from exc
    if out.returncode != 0:
        raise SystemExit(
            f"`databricks auth token` failed (profile={profile}). Re-run "
            f"`databricks auth login`. Detail:\n{out.stderr.strip()}"
        )
    try:
        return json.loads(out.stdout)["access_token"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise SystemExit(f"Could not parse OAuth token from CLI output: {exc}") from exc


def _resolve_auth_headers(args: argparse.Namespace) -> dict[str, str]:
    """Prefer an OAuth Bearer token (auto-refreshed by the CLI); fall back to cookie.

    An explicit cookie source (--cookie / --cookie-file / PB_COOKIE) wins if given,
    so cookie users are unaffected. Otherwise mint an OAuth token from the CLI."""
    cookie = args.cookie or (
        Path(args.cookie_file).read_text("utf-8").strip() if args.cookie_file else ""
    ) or os.environ.get("PB_COOKIE", "").strip()
    if cookie:
        return {"Cookie": f"__Host-databricksapps={cookie}"}

    token = _oauth_token_from_cli(args.databricks_profile, args.databricks_host)
    return {"Authorization": f"Bearer {token}"}


def _resolve_pb_url(args: argparse.Namespace) -> str:
    """Pick the platform backend base URL: --pb-url > PB_URL_<ENV> env var > PB_ENVS.

    PB_ENVS ships with placeholder hosts so no internal infrastructure is baked
    into the repo, so bail out with instructions if nothing replaced them."""
    url = args.pb_url or os.environ.get(f"PB_URL_{args.env.upper()}", "").strip() or PB_ENVS[args.env]
    if PB_URL_PLACEHOLDER in url:
        raise SystemExit(
            f"No platform backend URL configured for --env {args.env}. Pass --pb-url, "
            f"set PB_URL_{args.env.upper()}, or replace the placeholder in PB_ENVS."
        )
    return url


def _resolve_user_ids(args: argparse.Namespace) -> list[str]:
    """Collect the user ids to enumerate for --agent-id, from --user-id (repeatable)
    and --users-file (one id per line, '#' comments ok). De-duped, order preserved.
    Empty => caller-only (derived from the token)."""
    users: list[str] = list(args.user_id or [])
    if args.users_file:
        for line in Path(args.users_file).read_text("utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                users.append(line)
    seen: set[str] = set()
    out: list[str] = []
    for u in users:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _resolve_metric(args: argparse.Namespace):
    if args.metric_spec == "builtin":
        return wrap_metric(smoke_metric)
    root = Path(args.metric_project_root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return wrap_metric(resolve_callable(args.metric_spec))


async def _amain(args: argparse.Namespace) -> int:
    # URL first: it is a pure config check, so an unconfigured PB_ENVS placeholder
    # reports itself instead of hiding behind a failed OAuth round trip.
    pb_url = _resolve_pb_url(args)
    print(f"[env] {args.env if not args.pb_url else 'custom'} -> {pb_url}")
    headers = _resolve_auth_headers(args)
    client = httpx.AsyncClient(base_url=pb_url.rstrip("/"), timeout=args.timeout)
    try:
        # Per-agent scoping: resolve this agent's sessions up front. With supplied
        # --user-id/--users-file this spans MULTIPLE users (cross-user download);
        # with none it defaults to just the caller's own id.
        agent_sessions: list[dict[str, Any]] | None = None
        if args.agent_id:
            user_ids = _resolve_user_ids(args)
            if not user_ids:
                user_ids = [await _derive_user_id(client, headers)]
            agent_sessions = []
            for uid in user_ids:
                try:
                    u_sessions = await list_agent_sessions(client, headers, uid, args.agent_id, args.session_limit)
                except (httpx.HTTPError, httpx.TimeoutException) as exc:
                    print(f"      ! could not list sessions for {uid}: {type(exc).__name__}", file=sys.stderr)
                    continue
                for s in u_sessions:
                    s.setdefault("user_id", uid)
                agent_sessions.extend(u_sessions)
            print(f"[agent] {args.agent_id}: {len(agent_sessions)} session(s) across {len(user_ids)} user(s)")

        if args.list_sessions:
            if agent_sessions is not None:
                print(f"{len(agent_sessions)} session(s) for agent '{args.agent_id}':\n")
                for s in agent_sessions:
                    print(f"  {s.get('session_id')}  [{s.get('user_id')}]")
                    print(f"       {s.get('session_title')}")
            else:
                sessions = await list_sessions(client, headers, args.session_limit)
                print(f"{len(sessions)} session(s):\n")
                for s in sessions:
                    print(f"  msgs={s.get('message_count'):>2}  {s.get('id')}")
                    print(f"       {s.get('title')}")
            return 0

        if agent_sessions is not None:
            session_ids = [s["session_id"] for s in agent_sessions]
        elif args.all_sessions:
            sessions = await list_sessions(client, headers, args.session_limit)
            session_ids = [s["id"] for s in sessions]
        else:
            session_ids = list(args.session_id or [])
        if not session_ids:
            raise SystemExit("Provide --agent-id, --session-id (repeatable), --all-sessions, or --list-sessions.")

        cap = args.limit_traces if args.limit_traces and args.limit_traces > 0 else None
        print(
            f"[1/3] Importing {len(session_ids)} session(s) via source='{args.source}'"
            + (f" (cap {cap} traces)" if cap else "")
        )
        entries: list[_TraceEntry] = []
        src_counts = {"trajectories": 0, "messages": 0, "empty": 0, "error": 0}
        for sid in session_ids:
            try:
                sid_entries: list[_TraceEntry] = []
                used = "empty"
                if args.source in ("trajectories", "auto"):
                    traj_items = await _with_retry(fetch_trajectories, client, headers, sid)
                    sid_entries = entries_from_trajectories(sid, traj_items)
                    if sid_entries:
                        used = "trajectories"
                if not sid_entries and args.source in ("messages", "auto"):
                    messages = await _with_retry(fetch_messages, client, headers, sid)
                    sid_entries = entries_from_messages(sid, messages)
                    if sid_entries:
                        used = "messages"
                entries.extend(sid_entries)
                src_counts[used] += 1
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                src_counts["error"] += 1
                print(f"      ! skipped {sid}: {type(exc).__name__}", file=sys.stderr)
            if cap and len(entries) >= cap:
                del entries[cap:]
                print(f"      reached --limit-traces={cap}; stopping enumeration.")
                break
        print(
            f"      sources: {src_counts['trajectories']} via trajectories, "
            f"{src_counts['messages']} via messages, {src_counts['empty']} empty, "
            f"{src_counts['error']} errored"
        )
    except AuthWallError as exc:
        print(f"\nAUTH ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        await client.aclose()

    if not entries:
        print(
            f"No scorable traces found via source='{args.source}' "
            "(no stored trajectories and no planner steps in message metadata).",
            file=sys.stderr,
        )
        return 3

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, manifest = await build_dataset_rows(entries, args.split, args.agent_id)
    dataset_path = out_dir / "dataset.jsonl"
    with dataset_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )
    print(f"[2/3] Built {len(rows)} TraceExampleV1 row(s) -> {dataset_path}")

    print(f"[3/3] Scoring with metric '{args.metric_spec}' (no re-run)")
    harness = await run_harness_eval(
        dataset_path=dataset_path,
        output_dir=out_dir,
        run_one=_make_run_one(),
        metric=_resolve_metric(args),
        mode="baseline",
        pred_name="stored",
    )

    result_lines = Path(harness["results_path"]).read_text("utf-8").splitlines()
    results = [json.loads(line) for line in result_lines if line.strip()]
    csv_path = out_dir / "results.csv"
    _write_csv(csv_path, rows, results)
    mean = (sum(r["score"] for r in results) / len(results)) if results else 0.0
    print("\n=== RESULTS ===")
    for r in results:
        fb = f"  ({r['feedback']})" if r.get("feedback") else ""
        print(f"  score={r['score']:.2f}  {r['example_id']}{fb}")
    print(f"\nmean_score = {mean:.3f} over {len(results)} turn(s)")
    print(f"dataset:  {dataset_path}")
    print(f"manifest: {manifest_path}")
    print(f"results: {harness['results_path']}")
    print(f"csv:     {csv_path}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list-sessions", action="store_true", help="List your sessions and exit.")
    p.add_argument("--all-sessions", action="store_true",
                   help="Import every session returned by /sessions (USER-scoped, all agents mixed).")
    p.add_argument("--agent-id", default=None, metavar="AGENT",
                   help="Scope to ONE agent's sessions via /users/{uid}/agents/{agent}.")
    p.add_argument("--user-id", action="append", metavar="EMAIL", default=None,
                   help="User id(s) to enumerate for --agent-id (repeatable, CROSS-USER). "
                        "Default: just your own id (derived from your token).")
    p.add_argument("--users-file", default=None,
                   help="File with one user id (email) per line; merged with --user-id.")
    p.add_argument("--limit-traces", type=int, default=0,
                   help="Cap total traces downloaded across all users/sessions (0 = no cap).")
    p.add_argument("--session-id", action="append", metavar="SID", help="Session id to import (repeatable).")
    p.add_argument("--session-limit", type=int, default=50,
                   help="Max sessions to list/import; use 0 for ALL (default 50).")
    p.add_argument("--env", choices=sorted(PB_ENVS), default="dev",
                   help="Platform backend environment (default: dev). Ignored if --pb-url is given.")
    p.add_argument("--pb-url", default=None,
                   help="Explicit platform backend base URL; overrides --env.")
    p.add_argument("--databricks-profile", default=os.environ.get("PB_DATABRICKS_PROFILE", "DEFAULT"),
                   help="Databricks CLI profile to mint the OAuth Bearer token from "
                        "(env: PB_DATABRICKS_PROFILE, default: DEFAULT).")
    p.add_argument("--databricks-host", default=None,
                   help="Databricks host for the token (disambiguates when multiple profiles match).")
    p.add_argument("--cookie", default=None, help="__Host-databricksapps value — fallback if not using OAuth.")
    p.add_argument("--cookie-file", default=None, help="File containing the __Host-databricksapps value.")
    p.add_argument("--source", choices=("auto", "trajectories", "messages"), default="auto",
                   help="Where to read the trace: first-class state-store trajectories, "
                        "message metadata (historical), or auto (trajectories then fall back per session).")
    p.add_argument("--out", default="/tmp/imported_eval", help="Output dir for dataset + results.")
    p.add_argument("--metric-spec", default="builtin",
                   help="module:function metric, or 'builtin' for the agent-agnostic smoke metric (default).")
    p.add_argument("--metric-project-root", default=".", help="Dir added to sys.path so --metric-spec imports.")
    p.add_argument("--split", default="val", help="Split stamped on imported rows (default: val).")
    p.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout seconds.")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain(_parse_args())))
