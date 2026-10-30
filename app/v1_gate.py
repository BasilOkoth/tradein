"""Optional V1 threshold state machine. V1 score is NOT a probability."""

def transition(state: str, purchases: int, score: float | None, threshold: float):
    state = state if state in {"WAIT_HIGH", "ACTIVE", "WAIT_LOW"} else "WAIT_HIGH"
    purchases = max(0, min(2, int(purchases)))
    if state == "ACTIVE":
        return state, purchases, purchases < 2
    if state == "WAIT_LOW":
        if score is not None and score < threshold:
            return "WAIT_HIGH", 0, False
        return "WAIT_LOW", purchases, False
    if score is not None and score >= threshold:
        return "ACTIVE", 0, True
    return "WAIT_HIGH", 0, False
