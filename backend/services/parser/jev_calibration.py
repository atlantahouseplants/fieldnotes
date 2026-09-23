"""Calibration metrics for the Jev shadow replay — Brier score + reliability table.

Ground truth is OUR OWN stored parse (the HFT/Jev manual rule: measure the
model against OUR outcomes, never the vendor's self-report):

- For each Noul question (supplies_present / followups_present /
  customer_requests_present), the outcome is 1 if the stored parser's
  corresponding array (parsed_supplies / parsed_followups /
  parsed_customer_requests) was non-empty, else 0. The prediction is Jev's
  Noul probability (0..1).
- For the status Choice, the prediction is Jev's confidence and the outcome
  is 1 if Jev's chosen status matched the stored status, else 0.

All functions are pure (no I/O) so they can be unit-tested against synthetic
data and reused anywhere the raw probabilities + outcomes are available.
"""
from typing import Optional, Sequence


def brier_score(predictions: Sequence[float], outcomes: Sequence[float]) -> Optional[float]:
    """Mean squared error between predicted probabilities and binary outcomes.

    Returns None when there are no samples (so callers can distinguish
    "not computable" from a real 0.0 score).
    """
    if not predictions:
        return None
    if len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must be the same length")
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / len(predictions)


def reliability_table(
    predictions: Sequence[float],
    outcomes: Sequence[float],
    n_bins: int = 10,
) -> list[dict]:
    """Build an n-bin reliability table: per bin, the count, mean predicted
    probability, and observed outcome frequency.

    Bins are [i/n_bins, (i+1)/n_bins) for i in 0..n_bins-1; the top bin is
    closed at 1.0 so a prediction of exactly 1.0 lands in the last bin. A
    well-calibrated model has mean_predicted ≈ observed_frequency in every
    bin (the curve hugs the diagonal). Empty bins report nulls.
    """
    if len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must be the same length")

    bins: list[list] = [[] for _ in range(n_bins)]
    for p, o in zip(predictions, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append((p, o))

    table = []
    for i in range(n_bins):
        lo = i / n_bins
        hi = (i + 1) / n_bins
        if bins[i]:
            mean_pred = sum(p for p, _ in bins[i]) / len(bins[i])
            observed = sum(o for _, o in bins[i]) / len(bins[i])
            mean_pred = round(mean_pred, 4)
            observed = round(observed, 4)
        else:
            mean_pred = observed = None
        table.append({
            "bin": i,
            "range": [round(lo, 2), round(hi, 2)],
            "count": len(bins[i]),
            "mean_predicted": mean_pred,
            "observed_frequency": observed,
        })
    return table


def compute_calibration(results: Sequence[dict]) -> dict:
    """Aggregate Brier + reliability from a list of per-note replay rows.

    Each row dict is expected to carry (all optional, None-tolerant):
      supplies_present_prob / followups_present_prob / customer_requests_present_prob
        — Jev Noul probabilities.
      stored_supplies_nonempty / stored_followups_nonempty /
      stored_customer_requests_nonempty
        — booleans from OUR stored parse (array non-empty).
      status_confidence — Jev's status Choice confidence.
      status_agree — bool, Jev's status choice == stored status.

    Returns {brier_noul, brier_status, reliability_table, noul_sample_size,
             status_sample_size, note}. The note flags provisional results
    when the Noul sample size is under 100.
    """
    noul_preds: list[float] = []
    noul_outcomes: list[float] = []
    status_preds: list[float] = []
    status_outcomes: list[float] = []

    noul_pairs = (
        ("supplies_present_prob", "stored_supplies_nonempty"),
        ("followups_present_prob", "stored_followups_nonempty"),
        ("customer_requests_present_prob", "stored_customer_requests_nonempty"),
    )
    for r in results:
        for prob_key, truth_key in noul_pairs:
            p = r.get(prob_key)
            o = r.get(truth_key)
            if p is not None and o is not None:
                noul_preds.append(float(p))
                noul_outcomes.append(1.0 if o else 0.0)

        p = r.get("status_confidence")
        o = r.get("status_agree")
        if p is not None and o is not None:
            status_preds.append(float(p))
            status_outcomes.append(1.0 if o else 0.0)

    n = len(noul_preds)
    note = None
    if 0 < n < 100:
        note = (
            f"Calibration provisional — {n} Noul samples (< 100). "
            "Re-run once shadow mode has accumulated more notes before acting "
            "on these numbers."
        )

    return {
        "brier_noul": brier_score(noul_preds, noul_outcomes),
        "brier_status": brier_score(status_preds, status_outcomes),
        "reliability_table": reliability_table(noul_preds, noul_outcomes),
        "noul_sample_size": n,
        "status_sample_size": len(status_preds),
        "note": note,
    }
