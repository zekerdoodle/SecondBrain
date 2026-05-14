"""Safe-deploy tools.

The apply_patch_deploy MCP tool — replaces the disabled
.claude/scripts/apply_patch.py bash script. See
codebase/projects/active/apply-patch-mcp-tool.md for the project, and
codebase/projects/active/apply-patch-mcp-plan.md for the design.

Module layout:

    apply_patch_deploy.py — MCP tool entry; synchronous portion + worker spawn.
    manifest.py           — manifest reader, scope-check helpers.
    smoke.py              — smoke runner (Phase B). Scrubbed-env subprocess
                            with FILE-MARKER failure injection (NOT env-var).
    rollback.py           — positive-filter rollback (Phase B). No
                            `git reset --hard`, no force-push.
    worker.py             — daemonized worker (Phase B). Owns restart →
                            smoke → maybe rollback → maybe restart again
                            → maybe re-smoke. Invoked as a CLI from the
                            sync portion via subprocess.Popen with
                            start_new_session=True.
"""

# Import to trigger registration with the MCP tool registry.
from . import apply_patch_deploy as apply_patch_deploy_module  # noqa: F401

from .apply_patch_deploy import apply_patch_deploy

__all__ = ["apply_patch_deploy"]
