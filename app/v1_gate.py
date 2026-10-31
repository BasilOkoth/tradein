"""Optional V1 *minimum score* gate (scores are not probabilities).

A fresh batch may start whenever the current top-ranked V1 score is >=
the configured minimum. It does not require a below-to-above crossing.
One prequalified batch may finish its second purchase if the score changes.
"""

def transition(state: str, purchases: int, score: float | None, threshold: float):
    state = state if state in {"WAIT_HIGH", "ACTIVE", "WAIT_LOW"} else "WAIT_HIGH"
    purchases = max(0, min(2, int(purchases)))
    # The second purchase of an already admitted batch is authorized independently
    # of the current score, but a fresh batch must meet the minimum again.
    if state == "ACTIVE" and purchases == 1:
        return "ACTIVE", 1, True
    if score is not None and score >= threshold:
        return "ACTIVE", 0, True
    return "WAIT_HIGH", 0, False
