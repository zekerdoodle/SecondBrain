"""
CUA Orchestrator V2 — Thin wrapper that drives the autonomous CUA loop.

The orchestrator's role is now minimal:
1. Build the initial instruction (with browser rules and recovery context)
2. Call cua.run_task() which handles the entire autonomous loop
3. Log step events into a TaskReceipt
4. Save the receipt

The orchestrator does NOT inject messages into the CUA conversation.
The ONLY instruction method is _build_initial_instruction().
"""

import os
import sys
import json
import time
from dataclasses import dataclass, field
from typing import Optional, Literal
from datetime import datetime
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gemini_cua import GeminiCUA, CUAResponse, StepEvent


@dataclass
class StepLog:
    """Log entry for a single step"""
    step_number: int
    action_type: Optional[str]
    action_args: Optional[dict]
    status: str
    timestamp: str
    model_text: Optional[str] = None
    error: Optional[str] = None


@dataclass
class TaskReceipt:
    """Final receipt of task execution"""
    task_summary: str
    status: Literal["completed", "partial", "failed", "blocked"] = "partial"
    steps_taken: list[StepLog] = field(default_factory=list)
    final_state: str = ""
    gathered_info: dict = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    total_time_seconds: float = 0

    def to_dict(self) -> dict:
        return {
            "task_summary": self.task_summary,
            "status": self.status,
            "steps_taken": [
                {
                    "step": s.step_number,
                    "action": s.action_type,
                    "args": s.action_args,
                    "status": s.status,
                    "timestamp": s.timestamp,
                    "model_text": s.model_text,
                    "error": s.error,
                }
                for s in self.steps_taken
            ],
            "final_state": self.final_state,
            "gathered_info": self.gathered_info,
            "blockers": self.blockers,
            "total_time_seconds": self.total_time_seconds
        }

    def to_markdown(self) -> str:
        """Format receipt as readable markdown"""
        lines = [
            "# CUA Task Receipt",
            "",
            f"**Task:** {self.task_summary}",
            f"**Status:** {self.status.upper()}",
            f"**Duration:** {self.total_time_seconds:.1f}s",
            f"**Steps:** {len(self.steps_taken)}",
            "",
            "## Steps Taken",
            ""
        ]

        for step in self.steps_taken:
            status_icon = {
                "action": "[ACT]",
                "complete": "[DONE]",
                "error": "[ERR]",
                "text": "[TXT]",
            }.get(step.status, "[?]")

            action_desc = f"`{step.action_type}`" if step.action_type else "text response"
            lines.append(f"{step.step_number}. {status_icon} {action_desc}")
            if step.action_args:
                args_str = json.dumps(step.action_args)
                lines.append(f"   - Args: {args_str[:100]}")
            if step.model_text:
                lines.append(f"   - Text: {step.model_text[:150]}")
            if step.error:
                lines.append(f"   - Error: {step.error[:100]}")
            lines.append("")

        if self.final_state:
            lines.extend([
                "## Final State",
                "",
                self.final_state,
                ""
            ])

        if self.gathered_info:
            lines.extend([
                "## Gathered Information",
                "",
                "```json",
                json.dumps(self.gathered_info, indent=2),
                "```",
                ""
            ])

        if self.blockers:
            lines.extend([
                "## Blockers",
                ""
            ])
            for b in self.blockers:
                lines.append(f"- {b}")

        return "\n".join(lines)


class CUAOrchestrator:
    """
    Thin orchestrator V2 — builds the instruction and lets the CUA run autonomously.

    The orchestrator does NOT inject messages into the CUA conversation.
    Its only interactions with the CUA are:
    1. Build the initial instruction
    2. Call run_task() — start the autonomous loop
    3. Read results when it finishes
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        max_steps: int = 300,
        log_dir: Optional[str] = None
    ):
        self.api_key = api_key
        self.max_steps = max_steps
        self.log_dir = Path(log_dir or "/home/debian/second_brain/.claude/logs/cua")
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def execute_task(
        self,
        task: str,
        mode: Literal["trust", "ping", "foreground"] = "trust"
    ) -> TaskReceipt:
        """
        Execute a computer use task.

        Builds the initial instruction and lets the CUA run autonomously.
        The CUA handles its own loop, history, and error recovery internally.
        """
        start_time = time.time()
        task_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        receipt = TaskReceipt(task_summary=task)

        cua = GeminiCUA(api_key=self.api_key)

        # Build the full initial instruction
        full_instruction = self._build_initial_instruction(task)

        print(f"[Orchestrator] Starting task: {task[:100]}...")
        print(f"[Orchestrator] Model: {cua.model}, Max steps: {self.max_steps}")

        # Step event callback for logging
        def on_step(event: StepEvent):
            step_log = StepLog(
                step_number=event.step_number,
                action_type=event.action_type,
                action_args=event.action_args,
                status=event.status,
                timestamp=datetime.fromtimestamp(event.timestamp).isoformat() if event.timestamp else datetime.now().isoformat(),
                model_text=event.model_text,
                error=event.error,
            )
            receipt.steps_taken.append(step_log)

        # Let the CUA run autonomously
        try:
            response = cua.run_task(
                full_instruction,
                max_steps=self.max_steps,
                on_step=on_step,
            )
        except Exception as e:
            receipt.status = "failed"
            receipt.final_state = f"CUA crashed: {str(e)}"
            receipt.blockers.append(f"Exception: {str(e)[:300]}")
            receipt.total_time_seconds = time.time() - start_time
            self._save_receipt(task_id, receipt)
            print(f"[Orchestrator] Task CRASHED: {e}")
            return receipt

        # Map CUA response to receipt
        if response.status == "complete":
            receipt.status = "completed"
            receipt.final_state = response.model_text or "Task completed"
            print(f"[Orchestrator] Task COMPLETE: {receipt.final_state[:100]}")

        elif response.status == "blocked":
            receipt.status = "blocked"
            receipt.final_state = response.model_text or ""
            receipt.blockers.append(response.error_message or "Model stuck")
            print(f"[Orchestrator] Task BLOCKED: {response.error_message}")

        elif response.status == "error":
            receipt.status = "failed"
            receipt.final_state = response.error_message or "Unknown error"
            receipt.blockers.append(response.error_message or "Unknown error")
            print(f"[Orchestrator] Task FAILED: {response.error_message}")

        else:
            receipt.status = "partial"
            receipt.final_state = response.model_text or response.error_message or ""
            print(f"[Orchestrator] Task PARTIAL: {response.status}")

        # Include step log from CUA response if on_step didn't capture all steps
        # (step_log from CUA contains raw tuples for actions that happened between events)
        if response.step_log:
            receipt.gathered_info["raw_step_log"] = [
                {"step": s[0], "action": s[1], "args": s[2]}
                for s in response.step_log
            ]
            receipt.gathered_info["total_steps"] = response.steps_completed

        receipt.total_time_seconds = time.time() - start_time
        self._save_receipt(task_id, receipt)

        print(f"[Orchestrator] Task finished: {receipt.status} ({receipt.total_time_seconds:.1f}s, {len(receipt.steps_taken)} steps)")
        return receipt

    def _build_initial_instruction(self, task: str, recovery_context: str = "") -> str:
        """Build the initial instruction for the CUA.

        This is the ONLY instruction method. The orchestrator never injects
        follow-up messages — the CUA model drives itself via function_calls.
        """
        return f"""{recovery_context}
YOUR TASK:
{task}

BROWSER RULES:
- Google Chrome is ALREADY running. DO NOT open a new browser window.
- Work in the EXISTING Chrome window.
- To navigate: Ctrl+L to focus address bar, type URL, Enter.

EXECUTION RULES:
- Work through items ONE AT A TIME in order
- After completing each item, move to the NEXT item immediately
- If an action doesn't work after 2 attempts, try a different approach
- When ALL items are done, VERIFY your work before completing:
  1. Review what was requested vs what you actually did
  2. Confirm each requested item was fulfilled accurately, not approximately
  3. If anything doesn't match what was asked for, fix it
  4. Report any substitutions or ambiguities in your summary
- Then respond with text: "TASK COMPLETE" and a summary

Start now. Look at the screenshot and take the first action.""".strip()

    def _save_receipt(self, task_id: str, receipt: TaskReceipt) -> None:
        """Save receipt to log directory"""
        try:
            receipt_path = self.log_dir / f"receipt_{task_id}.json"
            with open(receipt_path, "w") as f:
                json.dump(receipt.to_dict(), f, indent=2)

            md_path = self.log_dir / f"receipt_{task_id}.md"
            with open(md_path, "w") as f:
                f.write(receipt.to_markdown())

            print(f"[Orchestrator] Receipt saved to {receipt_path}")
        except Exception as e:
            print(f"[Orchestrator] Failed to save receipt: {e}")


def run_cua_task(task: str, mode: str = "trust", max_steps: int = 300) -> TaskReceipt:
    """
    Main entry point for running a CUA task.

    Args:
        task: The task to execute
        mode: Invocation mode (trust, ping, foreground)
        max_steps: Maximum steps before timeout

    Returns:
        TaskReceipt with execution results
    """
    orchestrator = CUAOrchestrator(max_steps=max_steps)
    return orchestrator.execute_task(task, mode=mode)


def main():
    """CLI interface for orchestrator"""
    import argparse

    parser = argparse.ArgumentParser(description="CUA Orchestrator V2")
    parser.add_argument("task", help="Task to execute")
    parser.add_argument("--mode", choices=["trust", "ping", "foreground"], default="trust")
    parser.add_argument("--max-steps", type=int, default=300)
    parser.add_argument("--output", help="Output file for receipt (markdown)")

    args = parser.parse_args()

    orchestrator = CUAOrchestrator(max_steps=args.max_steps)
    receipt = orchestrator.execute_task(args.task, mode=args.mode)

    # Output receipt
    md = receipt.to_markdown()
    print("\n" + "=" * 60)
    print(md)
    print("=" * 60)

    if args.output:
        with open(args.output, "w") as f:
            f.write(md)
        print(f"\nReceipt saved to: {args.output}")

    return 0 if receipt.status == "completed" else 1


if __name__ == "__main__":
    exit(main())
