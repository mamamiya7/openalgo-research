"""Bounded visual comparison from already calculated, matching-period evidence."""

import math

from research.evaluation_basis import comparison_status

VERSION = "research-automatic-comparison-v1"
MAX_POINTS = 2000


def build(original, selected):
    unavailable = {"version": VERSION, "status": "unavailable"}
    if not original or not selected:
        return {**unavailable, "reason": "Matching recorded curves are unavailable."}
    left, right = original.get("evaluation_basis"), selected.get("evaluation_basis")
    compatible = comparison_status(left, right)
    if not compatible["compatible"]:
        return {
            **unavailable,
            "reason": "Recorded comparison basis differs: " + ", ".join(compatible["differences"]),
        }
    capital = left["comparison"].get("capital")
    if not isinstance(capital, (int, float)) or not math.isfinite(capital) or capital <= 0:
        return {**unavailable, "reason": "Recorded starting cash is unavailable."}
    curves = [report.get("equity_curve", []) for report in (original, selected)]
    timestamps = [
        [point.get("timestamp", point.get("date")) for point in curve] for curve in curves
    ]
    if (
        not timestamps[0]
        or timestamps[0] != timestamps[1]
        or any(not stamp for stamp in timestamps[0])
    ):
        return {**unavailable, "reason": "Recorded curves do not share the same observation times."}
    if any(
        not isinstance(point.get("equity"), (int, float)) or not math.isfinite(point["equity"])
        for curve in curves
        for point in curve
    ):
        return {**unavailable, "reason": "A recorded equity value is unavailable."}
    total = len(curves[0])
    indices = (
        sorted(
            {
                round(i * (total - 1) / (min(total, MAX_POINTS) - 1))
                for i in range(min(total, MAX_POINTS))
            }
        )
        if total > 1
        else [0]
    )
    return {
        "version": VERSION,
        "status": "available",
        "period": left["period"],
        "evidence_id": left["evidence_id"],
        "comparison_id": left["comparison"]["id"],
        "observations": total,
        "displayed": len(indices),
        "chart": {
            "id": "automatic-final-comparison",
            "title": "Final check · original and selected",
            "status": "available",
            "figure": {
                "data": [
                    {
                        "type": "scatter",
                        "mode": "lines",
                        "name": label,
                        "x": [timestamps[0][i] for i in indices],
                        "y": [100 * (curve[i]["equity"] / capital - 1) for i in indices],
                    }
                    for label, curve in zip(("Original", "Selected"), curves, strict=True)
                ],
                "layout": {
                    "yaxis": {"title": {"text": "Account return (%)"}},
                    "xaxis": {"title": {"text": "Recorded observation"}},
                    "hovermode": "x unified",
                },
            },
        },
    }
