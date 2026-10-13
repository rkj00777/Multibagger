"""Fail-closed production promotion policy for the Multibagger Engine.

Candidate-level gates can qualify a name for shadow review. Production promotion
also requires independent, out-of-sample model validation. A successful workflow
run or a non-empty candidate list is not evidence of predictive skill.
"""
from __future__ import annotations

import json
from pathlib import Path


REQUIRED_HORIZONS = ("6m_126_sessions", "12m_252_sessions")


def production_validation_status(path="reports/multibagger-pit-validation.json") -> dict:
    """Return a conservative validation decision and machine-readable reasons."""
    report_path = Path(path)
    if not report_path.exists():
        return {"approved": False, "reason": "VALIDATION_REPORT_MISSING", "independent_dates": {}}
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return {"approved": False, "reason": "VALIDATION_REPORT_UNREADABLE", "independent_dates": {}}

    horizons = report.get("horizon_validation")
    if not isinstance(horizons, dict):
        return {"approved": False, "reason": "HORIZON_VALIDATION_MISSING", "independent_dates": {}}

    # The fundamental and MBE-composite proxy tests do not include a complete
    # historical catalyst archive. Record this as an independent blocking reason
    # while preserving diagnostics for the sample-size and return gates below.
    full_model_validated = report.get("full_model_validation_status") == "FULL_MODEL_VALIDATED"

    dates = {}
    reasons = []
    for horizon in REQUIRED_HORIZONS:
        result = horizons.get(horizon)
        if not isinstance(result, dict):
            reasons.append(f"{horizon}:MISSING")
            continue
        stats = result.get("statistical_validation") or {}
        n = stats.get("independent_decision_dates", result.get("decision_dates", 0))
        try:
            n = int(n or 0)
        except (TypeError, ValueError):
            n = 0
        dates[horizon] = n
        lift_low = result.get("selection_lift_ci_low")
        if lift_low is None:
            lift_ci = stats.get("selection_lift_ci") or {}
            lift_low = lift_ci.get("difference_ci_low")
        try:
            lift_low = float(lift_low)
        except (TypeError, ValueError):
            lift_low = None
        if n < 8:
            reasons.append(f"{horizon}:NEED_8_INDEPENDENT_DATES_HAVE_{n}")
        if result.get("status") != "VALIDATED":
            reasons.append(f"{horizon}:STATUS_{result.get('status', 'MISSING')}")
        if lift_low is None or lift_low <= 0:
            reasons.append(f"{horizon}:LIFT_CONFIDENCE_INTERVAL_NOT_POSITIVE")

    if not full_model_validated:
        reasons.append("FULL_MODEL_VALIDATION_INCOMPLETE_HISTORICAL_CATALYST_ARCHIVE")
    approved = not reasons and report.get("status") == "VALIDATED_FOR_PRODUCTION"
    if not approved and not reasons:
        reasons.append(f"REPORT_STATUS_{report.get('status', 'MISSING')}")
    return {
        "approved": approved,
        "reason": "VALIDATION_GATE_PASSED" if approved else "|".join(reasons),
        "independent_dates": dates,
    }
