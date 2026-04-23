"""
Transaction categorizer — merchant→category mapping layer on top of Plaid.

Plaid returns "Uncategorized" for ~everything, so we maintain our own
merchant pattern list and apply it whenever we read transactions.

## The match → override → unknown → learn loop

Every txn goes through this pipeline:

1. **Override check** — `txn_overrides.json` holds per-txn_id re-tags. If the
   specific Plaid txn_id has been overridden by the user (e.g. "this Amazon charge
   was a birthday gift, reclass as 'gift'"), use that and stop.

2. **Merchant match** — scan `merchants.json` patterns (case-insensitive
   regex against merchant_name, falling back to name). First match wins.
   Returns the pattern's category + confidence + reimbursable + alert flags.

3. **Unknown fallback** — no override, no pattern match → category "unknown".
   These get surfaced to the user in the daily ping so he can label once.

4. **Learn** — when the user labels an unknown in the ping, Finance calls
   `finance_add_merchant` to append a rule. Future txns from that merchant
   are auto-categorized. This is how the system gets smarter over time.

## Files

- `20_Areas/finance/merchants.json` — merchant patterns + category caps
- `20_Areas/finance/txn_overrides.json` — per-txn_id overrides

## Caching

Regexes are compiled once and cached in-process. We check the file mtime on
every call; if the file changed on disk, we reload. Atomic writes (tmp + rename)
protect against concurrent agents stomping each other.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mcp_tools.finance.categorizer")

# Paths — resolved relative to project root
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
MERCHANTS_PATH = os.path.join(_REPO_ROOT, "20_Areas", "finance", "merchants.json")
OVERRIDES_PATH = os.path.join(_REPO_ROOT, "20_Areas", "finance", "txn_overrides.json")

# In-process caches
_merchants_cache: Dict[str, Any] = {"mtime": 0, "data": None, "compiled": []}
_overrides_cache: Dict[str, Any] = {"mtime": 0, "data": None}


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    """Write JSON atomically (tmp file + rename). Prevents torn files."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    dir_ = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=dir_)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _default_merchants_doc() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated": datetime.utcnow().isoformat(timespec="seconds"),
        "merchants": [],
        "categories": {"unknown": {}},
    }


def _default_overrides_doc() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated": datetime.utcnow().isoformat(timespec="seconds"),
        "overrides": {},
    }


def load_merchants() -> Dict[str, Any]:
    """
    Load merchants.json, compile regexes, cache by mtime.

    Returns the raw doc. Use `_compiled_patterns()` for regex matching.
    """
    try:
        mtime = os.path.getmtime(MERCHANTS_PATH) if os.path.exists(MERCHANTS_PATH) else 0
    except OSError:
        mtime = 0

    if _merchants_cache["data"] is not None and _merchants_cache["mtime"] == mtime:
        return _merchants_cache["data"]

    if not os.path.exists(MERCHANTS_PATH):
        logger.warning(f"merchants.json not found at {MERCHANTS_PATH}, using empty default")
        data = _default_merchants_doc()
    else:
        try:
            with open(MERCHANTS_PATH, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to read merchants.json: {e}. Using empty default.")
            data = _default_merchants_doc()

    # Compile regexes once, skip broken ones
    compiled: List[Tuple[re.Pattern, Dict[str, Any]]] = []
    for entry in data.get("merchants", []):
        pattern = entry.get("pattern")
        if not pattern:
            continue
        try:
            rx = re.compile(pattern, re.IGNORECASE)
            compiled.append((rx, entry))
        except re.error as e:
            logger.warning(f"Skipping bad regex '{pattern}': {e}")

    _merchants_cache["mtime"] = mtime
    _merchants_cache["data"] = data
    _merchants_cache["compiled"] = compiled
    return data


def _compiled_patterns() -> List[Tuple[re.Pattern, Dict[str, Any]]]:
    """Get compiled patterns (forces load if stale)."""
    load_merchants()
    return _merchants_cache["compiled"]


def load_overrides() -> Dict[str, Any]:
    """Load txn_overrides.json, cache by mtime."""
    try:
        mtime = os.path.getmtime(OVERRIDES_PATH) if os.path.exists(OVERRIDES_PATH) else 0
    except OSError:
        mtime = 0

    if _overrides_cache["data"] is not None and _overrides_cache["mtime"] == mtime:
        return _overrides_cache["data"]

    if not os.path.exists(OVERRIDES_PATH):
        data = _default_overrides_doc()
    else:
        try:
            with open(OVERRIDES_PATH, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to read txn_overrides.json: {e}. Using empty default.")
            data = _default_overrides_doc()

    _overrides_cache["mtime"] = mtime
    _overrides_cache["data"] = data
    return data


# ---------------------------------------------------------------------------
# Core categorization
# ---------------------------------------------------------------------------

def _match_string(txn: Dict[str, Any]) -> str:
    """Build the string we match patterns against — merchant_name preferred, else name."""
    return str(txn.get("merchant_name") or txn.get("name") or "")


def categorize_transaction(txn: Dict[str, Any]) -> Dict[str, Any]:
    """
    Categorize a single Plaid transaction.

    Returns a shallow copy of the txn with these added fields:
      - sb_category (str)           — our assigned category (never missing)
      - sb_confidence (str)         — "locked", "learn", "override", "unknown"
      - sb_reimbursable (bool)
      - sb_alert (bool)
      - matched_pattern (str|None)  — the regex source that matched, or None
      - sb_note (str|None)          — optional note from the merchant rule or override
    """
    out = dict(txn)
    txn_id = txn.get("transaction_id") or txn.get("id")

    # 1. Override check (highest priority)
    overrides = load_overrides().get("overrides", {})
    if txn_id and txn_id in overrides:
        ov = overrides[txn_id]
        out["sb_category"] = ov.get("category", "unknown")
        out["sb_confidence"] = "override"
        out["sb_reimbursable"] = bool(ov.get("reimbursable", False))
        out["sb_alert"] = bool(ov.get("alert", False))
        out["matched_pattern"] = None
        out["sb_note"] = ov.get("note")
        return out

    # 2. Merchant pattern match
    haystack = _match_string(txn)
    for rx, entry in _compiled_patterns():
        if rx.search(haystack):
            out["sb_category"] = entry.get("category", "unknown")
            out["sb_confidence"] = entry.get("confidence", "locked")
            out["sb_reimbursable"] = bool(entry.get("reimbursable", False))
            out["sb_alert"] = bool(entry.get("alert", False))
            out["matched_pattern"] = entry.get("pattern")
            out["sb_note"] = entry.get("note")
            return out

    # 3. Unknown fallback
    out["sb_category"] = "unknown"
    out["sb_confidence"] = "unknown"
    out["sb_reimbursable"] = False
    out["sb_alert"] = False
    out["matched_pattern"] = None
    out["sb_note"] = None
    return out


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def categorize_batch(txns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Categorize a batch of txns. Returns structured report.

    Only positive-amount, non-pending txns count toward category totals
    (Plaid convention: positive = money out / spending, negative = income).

    Returns:
      {
        "categorized": [<txn with sb_* fields>, ...],
        "unknown": [<txn with sb_category == "unknown">, ...],
        "alerts": [<txn with sb_alert == True>, ...],
        "summary_by_category": {cat: {"count": N, "total": float}, ...}
      }
    """
    categorized: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []
    alerts: List[Dict[str, Any]] = []
    summary: Dict[str, Dict[str, float]] = defaultdict(lambda: {"count": 0, "total": 0.0})

    for raw in txns or []:
        if not isinstance(raw, dict):
            continue
        t = categorize_transaction(raw)
        categorized.append(t)

        amt = _safe_float(t.get("amount"))
        pending = bool(t.get("pending"))
        cat = t.get("sb_category", "unknown")

        # Only count spending (positive, non-pending) toward category totals
        if not pending and amt > 0:
            summary[cat]["count"] += 1
            summary[cat]["total"] += amt

        if cat == "unknown" and not pending and amt > 0:
            unknown.append(t)

        if t.get("sb_alert") and not pending and amt > 0:
            alerts.append(t)

    # Round totals
    summary_clean: Dict[str, Dict[str, Any]] = {
        cat: {"count": int(v["count"]), "total": round(v["total"], 2)}
        for cat, v in summary.items()
    }

    return {
        "categorized": categorized,
        "unknown": unknown,
        "alerts": alerts,
        "summary_by_category": summary_clean,
    }


# ---------------------------------------------------------------------------
# Report formatting — consumable by Finance agent in daily ping
# ---------------------------------------------------------------------------

def format_summary_report(
    batch_result: Dict[str, Any],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    """
    Render a human-readable category summary with cap % and alerts.
    Designed for the Finance agent to paste ~as-is into the daily ping.
    """
    doc = load_merchants()
    cat_meta = doc.get("categories", {}) or {}
    summary = batch_result.get("summary_by_category", {}) or {}
    unknown = batch_result.get("unknown", []) or []
    alerts = batch_result.get("alerts", []) or []

    period = ""
    if start_date or end_date:
        period = f" ({start_date or '?'} → {end_date or '?'})"

    lines = [f"SUMMARY{period}:"]

    # Category lines — cap-aware
    # Order: categories in summary first (by total desc), then zero-spend caps
    seen = set()
    sorted_cats = sorted(summary.items(), key=lambda kv: kv[1]["total"], reverse=True)
    for cat, stats in sorted_cats:
        if cat == "unknown":
            # Rendered separately below with the detail list
            continue
        seen.add(cat)
        total = stats["total"]
        meta = cat_meta.get(cat, {})
        cap = meta.get("monthly_cap")
        weekly_cap = meta.get("weekly_cap")
        alert_on_any = meta.get("alert_on_any")

        if alert_on_any and total > 0:
            lines.append(f"  {cat:<14}: ${total:<8,.2f} ALERT (any charge triggers)")
        elif cap is not None:
            pct = (total / cap * 100) if cap > 0 else float("inf")
            warn = ""
            if cap == 0 and total > 0:
                warn = " 🚨"
            elif cap > 0 and pct >= 100:
                warn = " 🚨"
            elif cap > 0 and pct >= 90:
                warn = " ⚠️"
            pct_str = f"{pct:.0f}%" if cap > 0 else "∞"
            lines.append(f"  {cat:<14}: ${total:<8,.2f} / ${cap} cap  ({pct_str}){warn}")
        elif weekly_cap is not None:
            lines.append(f"  {cat:<14}: ${total:<8,.2f} / ${weekly_cap}/wk cap")
        else:
            lines.append(f"  {cat:<14}: ${total:<8,.2f}")

    # Unknown
    if unknown:
        unknown_total = sum(_safe_float(t.get("amount")) for t in unknown)
        lines.append(
            f"  {'unknown':<14}: ${unknown_total:<8,.2f} across {len(unknown)} txns — see list below"
        )

    # Unknown detail list
    if unknown:
        lines.append("")
        lines.append("UNKNOWN TXNS (need the user input):")
        for t in unknown:
            date = t.get("date", "?")
            name = t.get("merchant_name") or t.get("name") or "?"
            amt = _safe_float(t.get("amount"))
            tid = t.get("transaction_id") or t.get("id") or "?"
            lines.append(f"  {date}  \"{name}\"  -${amt:,.2f}  [id={tid}]")

    # Alerts
    lines.append("")
    lines.append("ALERTS:")
    if alerts:
        for t in alerts:
            date = t.get("date", "?")
            name = t.get("merchant_name") or t.get("name") or "?"
            amt = _safe_float(t.get("amount"))
            cat = t.get("sb_category", "?")
            lines.append(f"  🚨 {date}  \"{name}\"  -${amt:,.2f}  [{cat}]")
    else:
        lines.append("  [none]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mutators — exposed via MCP tools
# ---------------------------------------------------------------------------

def add_merchant(
    pattern: str,
    category: str,
    confidence: str = "locked",
    note: Optional[str] = None,
    reimbursable: bool = False,
    alert: bool = False,
) -> Dict[str, Any]:
    """
    Append a merchant rule to merchants.json. Atomic write.

    Validates the regex before writing. Returns the new entry.
    Raises ValueError on invalid regex.
    """
    if not pattern or not isinstance(pattern, str):
        raise ValueError("pattern must be a non-empty string")
    if not category or not isinstance(category, str):
        raise ValueError("category must be a non-empty string")

    # Validate regex
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern '{pattern}': {e}")

    # Load fresh (bypass cache to avoid stomping concurrent write)
    if os.path.exists(MERCHANTS_PATH):
        with open(MERCHANTS_PATH, "r") as f:
            doc = json.load(f)
    else:
        doc = _default_merchants_doc()

    entry = {
        "pattern": pattern,
        "category": category,
        "confidence": confidence,
    }
    if note:
        entry["note"] = note
    if reimbursable:
        entry["reimbursable"] = True
    if alert:
        entry["alert"] = True

    doc.setdefault("merchants", []).append(entry)
    doc["updated"] = datetime.utcnow().isoformat(timespec="seconds")

    _atomic_write_json(MERCHANTS_PATH, doc)

    # Invalidate cache
    _merchants_cache["mtime"] = 0
    _merchants_cache["data"] = None
    _merchants_cache["compiled"] = []

    return entry


def override_txn_category(
    txn_id: str,
    new_category: str,
    note: Optional[str] = None,
    reimbursable: bool = False,
    alert: bool = False,
) -> Dict[str, Any]:
    """
    Record a per-txn override (e.g. "this specific Amazon charge was a gift").
    Does NOT touch the merchant rules. Atomic write.
    """
    if not txn_id or not isinstance(txn_id, str):
        raise ValueError("txn_id must be a non-empty string")
    if not new_category or not isinstance(new_category, str):
        raise ValueError("new_category must be a non-empty string")

    if os.path.exists(OVERRIDES_PATH):
        with open(OVERRIDES_PATH, "r") as f:
            doc = json.load(f)
    else:
        doc = _default_overrides_doc()

    entry = {
        "category": new_category,
        "set_at": datetime.utcnow().isoformat(timespec="seconds"),
    }
    if note:
        entry["note"] = note
    if reimbursable:
        entry["reimbursable"] = True
    if alert:
        entry["alert"] = True

    doc.setdefault("overrides", {})[txn_id] = entry
    doc["updated"] = datetime.utcnow().isoformat(timespec="seconds")

    _atomic_write_json(OVERRIDES_PATH, doc)

    # Invalidate cache
    _overrides_cache["mtime"] = 0
    _overrides_cache["data"] = None

    return entry
