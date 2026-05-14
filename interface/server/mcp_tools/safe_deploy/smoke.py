"""
Smoke runner for apply_patch_deploy (Phase B).

This module runs a small live-route health check against the post-restart
server. It is designed to be invoked AS A SUBPROCESS with a SCRUBBED
ENVIRONMENT, so that test-only state from the calling process cannot leak
into the smoke run.

Two ways to invoke:

    # As a CLI (this is what the worker uses):
    python smoke.py                # → prints JSON to stdout
        env: PATH, HOME, optionally SERVER_BASE (everything else stripped)

    # Programmatically (for isolation tests):
    from smoke import run_smoke_scrubbed, run_smoke_inline
    result = run_smoke_scrubbed()  # spawns a scrubbed subprocess
    result = run_smoke_inline()    # runs in-process (no env hygiene)

Failure-injection contract:

    The smoke runner reads `.claude/apply_patch_smoke_force_fail` if present
    (any content), DELETES IT ON ENTRY (one-shot, read-and-delete), and
    treats the run as a forced failure with `first_failure="force_fail_marker"`.

    No environment variables are involved. This is the structural fix for
    the 2026-05-12 incident where APPLY_PATCH_FORCE_SMOKE_FAIL leaked
    through a restart and caused the rolled-back code to also fail smoke,
    producing a FATAL state on a healthy tree.

    The marker file is intentionally one-shot — it survives at most one
    smoke invocation. To force BOTH the initial smoke AND the rollback
    re-smoke to fail (for testing the FATAL_rollback_smoke_failed path),
    the marker must be re-created between the two invocations.

Smoke checks (Phase B minimum):

    1. HTTP 200 on /api/agents (known-healthy route + agent registry).
    2. MCP tool registry loads via /api/tools/categories (non-empty).
    3. Agent prompt-build round-trip via /api/agents/patch (returns the
       agent's prompt content — verifies agent loader works).

    More checks can be added without breaking the wire format: each check
    has {"name", "ok", "detail"} and the runner reports {"passed",
    "first_failure", "elapsed_sec", "checks"}.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple


REPO_ROOT = Path("/home/debian/second_brain")
FORCE_FAIL_MARKER = REPO_ROOT / ".claude" / "apply_patch_smoke_force_fail"
DEFAULT_SERVER_BASE = "http://localhost:8000"
SMOKE_BUDGET_SEC = 30


# --------------------------------------------------------------------------- helpers


def _http_get(server_base: str, path: str, timeout: float = 3.0) -> Tuple[int, bytes]:
    url = server_base.rstrip("/") + path
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, ConnectionResetError, OSError):
        return 0, b""


def _consume_force_fail_marker() -> Tuple[bool, str]:
    """Read and delete the force-fail marker if present.

    Returns (forced, detail). One-shot semantics: the marker is removed
    on the same call that sees it.
    """
    try:
        if not FORCE_FAIL_MARKER.exists():
            return False, ""
        content = ""
        try:
            content = FORCE_FAIL_MARKER.read_text().strip()
        except Exception:
            pass
        try:
            FORCE_FAIL_MARKER.unlink()
        except FileNotFoundError:
            pass
        return True, content or "(empty marker)"
    except Exception as e:
        # If we can't read the marker, don't fail — that would block real
        # deploys. Log to stderr and proceed.
        sys.stderr.write(f"smoke: force-fail marker read error: {e}\n")
        return False, ""


# --------------------------------------------------------------------------- waiting


def wait_for_server_up(server_base: str, timeout_sec: int = 25) -> Tuple[bool, float]:
    """Poll /api/agents until 200, up to timeout_sec. Returns (ok, elapsed)."""
    start = time.monotonic()
    deadline = start + timeout_sec
    while time.monotonic() < deadline:
        code, _ = _http_get(server_base, "/api/agents", timeout=2.0)
        if code == 200:
            return True, time.monotonic() - start
        time.sleep(1.0)
    return False, time.monotonic() - start


# --------------------------------------------------------------------------- core run


def run_smoke_inline(server_base: str = DEFAULT_SERVER_BASE) -> Dict[str, Any]:
    """Run the smoke suite in-process. No env hygiene.

    Use run_smoke_scrubbed() from the worker — that wraps this in a
    subprocess with a whitelisted env.
    """
    started = time.monotonic()
    budget_deadline = started + SMOKE_BUDGET_SEC
    checks: List[Dict[str, Any]] = []
    first_failure: str | None = None

    def record(name: str, ok: bool, detail: str = "") -> None:
        nonlocal first_failure
        checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok and first_failure is None:
            first_failure = name

    # ---- 0. Force-fail marker (consumed FIRST, regardless of outcome) ----
    forced, marker_detail = _consume_force_fail_marker()
    if forced:
        # We still run the rest of the suite for forensics, but the verdict
        # is locked to fail with first_failure=force_fail_marker.
        record(
            "force_fail_marker",
            False,
            f"marker present (one-shot consumed): {marker_detail}",
        )

    # ---- 1. /api/agents — known-healthy route + agent registry ----------
    if time.monotonic() >= budget_deadline:
        record("agents_http_200", False, "budget exhausted before check")
    else:
        code, body = _http_get(server_base, "/api/agents", timeout=5.0)
        if code != 200:
            record("agents_http_200", False, f"got HTTP {code}")
        else:
            try:
                data = json.loads(body)
                agents = data.get("agents", [])
                if not isinstance(agents, list) or len(agents) == 0:
                    record(
                        "agents_http_200",
                        False,
                        f"agent registry empty or wrong shape",
                    )
                else:
                    record(
                        "agents_http_200",
                        True,
                        f"agent registry: {len(agents)} agents",
                    )
            except Exception as e:
                record("agents_http_200", False, f"parse error: {e}")

    # ---- 2. /api/tools/categories — MCP tool registry --------------------
    if time.monotonic() >= budget_deadline:
        record("mcp_tool_registry", False, "budget exhausted before check")
    else:
        code, body = _http_get(server_base, "/api/tools/categories", timeout=5.0)
        if code != 200:
            record("mcp_tool_registry", False, f"got HTTP {code}")
        else:
            try:
                data = json.loads(body)
                cats = data.get("categories", [])
                tool_count = sum(len(c.get("tools", [])) for c in cats)
                if tool_count == 0:
                    record("mcp_tool_registry", False, "tool count is zero")
                else:
                    record(
                        "mcp_tool_registry",
                        True,
                        f"{tool_count} tools across {len(cats)} categories",
                    )
            except Exception as e:
                record("mcp_tool_registry", False, f"parse error: {e}")

    # ---- 3. /api/agents/patch — agent prompt-build round-trip ------------
    # Verifies the agent loader can read config + prompt for an agent.
    # We pick `patch` because it's the agent that invokes the deploy tool —
    # if its loader breaks, the next deploy can't be ordered.
    if time.monotonic() >= budget_deadline:
        record("agent_prompt_build", False, "budget exhausted before check")
    else:
        code, body = _http_get(server_base, "/api/agents/patch", timeout=5.0)
        if code != 200:
            record("agent_prompt_build", False, f"got HTTP {code}")
        else:
            try:
                data = json.loads(body)
                prompt = data.get("prompt", "")
                cfg = data.get("config", {})
                if not isinstance(prompt, str) or len(prompt) < 100:
                    record(
                        "agent_prompt_build",
                        False,
                        f"prompt content too small (len={len(prompt) if isinstance(prompt, str) else 'N/A'})",
                    )
                elif not isinstance(cfg, dict) or not cfg:
                    record(
                        "agent_prompt_build",
                        False,
                        "config block empty/wrong shape",
                    )
                else:
                    record(
                        "agent_prompt_build",
                        True,
                        f"patch prompt OK (len={len(prompt)})",
                    )
            except Exception as e:
                record("agent_prompt_build", False, f"parse error: {e}")

    elapsed = time.monotonic() - started
    passed = first_failure is None
    return {
        "passed": passed,
        "first_failure": first_failure,
        "elapsed_sec": round(elapsed, 2),
        "checks": checks,
    }


# --------------------------------------------------------------------------- scrubbed runner


_SMOKE_ENV_WHITELIST = ("PATH", "HOME", "LANG", "LC_ALL")


def run_smoke_scrubbed(
    server_base: str = DEFAULT_SERVER_BASE,
    timeout_sec: int = 60,
) -> Dict[str, Any]:
    """Run the smoke suite in a scrubbed-env subprocess.

    This is the public API the worker uses. The subprocess inherits ONLY
    PATH, HOME, LANG, LC_ALL, plus SERVER_BASE (which we set explicitly).
    Every other env var from the parent is stripped, so test-only state
    cannot leak in.

    Returns the parsed JSON result, or a synthetic failure on error.
    """
    scrub_env: Dict[str, str] = {}
    for key in _SMOKE_ENV_WHITELIST:
        if key in os.environ:
            scrub_env[key] = os.environ[key]
    scrub_env["SERVER_BASE"] = server_base
    # Explicit defenses: never inherit Python-influencing vars that could
    # change the smoke subprocess's behavior.
    # (Belt + suspenders — the whitelist already excludes them, but if
    # someone extends the whitelist sloppily, this catches the obvious mistakes.)
    for danger in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV"):
        scrub_env.pop(danger, None)

    smoke_py = str(Path(__file__).resolve())
    try:
        proc = subprocess.run(
            [sys.executable, smoke_py],
            env=scrub_env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=str(REPO_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "first_failure": "smoke_subprocess_timeout",
            "elapsed_sec": timeout_sec,
            "checks": [
                {
                    "name": "smoke_subprocess_timeout",
                    "ok": False,
                    "detail": f"smoke subprocess exceeded {timeout_sec}s",
                }
            ],
        }
    except Exception as e:
        return {
            "passed": False,
            "first_failure": "smoke_subprocess_launch_failed",
            "elapsed_sec": 0,
            "checks": [
                {
                    "name": "smoke_subprocess_launch_failed",
                    "ok": False,
                    "detail": f"{type(e).__name__}: {e}",
                }
            ],
        }

    # Non-zero exit is EXPECTED on smoke failure — the CLI exits 1 when
    # passed=False. The JSON on stdout is the truth; only fall back to a
    # synthetic failure if stdout is empty or unparseable.
    stdout_stripped = proc.stdout.strip()
    if stdout_stripped:
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            pass  # fall through to synthetic failure below
    return {
        "passed": False,
        "first_failure": "smoke_subprocess_no_json",
        "elapsed_sec": 0,
        "checks": [
            {
                "name": "smoke_subprocess_no_json",
                "ok": False,
                "detail": (
                    f"exit {proc.returncode}; stdout had no parseable JSON; "
                    f"stderr={proc.stderr.strip()[:500]!r}; "
                    f"stdout={stdout_stripped[:500]!r}"
                ),
            }
        ],
    }


# --------------------------------------------------------------------------- CLI entry


if __name__ == "__main__":
    server_base = os.environ.get("SERVER_BASE", DEFAULT_SERVER_BASE)
    result = run_smoke_inline(server_base=server_base)
    print(json.dumps(result, indent=2, sort_keys=True))
    sys.exit(0 if result["passed"] else 1)
