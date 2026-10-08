from collections import Counter
import math

class Top4DigitScorer:
    """
    Lightweight V1-style teaching scorer.

    This branch is intentionally self-contained. It ranks all ten digits from
    recent canonical last-digit history. The four highest-scoring digits form
    the next basket. Replace this scorer with the original DigitScoreEngine if
    you want byte-for-byte scoring parity with another branch.
    """

    def __init__(self, max_history=100, min_history=20):
        self.max_history = int(max_history)
        self.min_history = int(min_history)

    @staticmethod
    def _gap(history, digit, cap=50):
        for i, d in enumerate(reversed(history)):
            if d == digit:
                return min(i, cap)
        return cap

    @staticmethod
    def _freq(history, digit, window):
        h = history[-window:]
        return sum(1 for x in h if x == digit) / len(h) if h else 0.0

    @staticmethod
    def _transition(history, digit):
        if len(history) < 2:
            return 0.1
        current = history[-1]
        pairs = [(a,b) for a,b in zip(history[:-1], history[1:]) if a == current]
        hits = sum(1 for _,b in pairs if b == digit)
        return (hits + 1.0) / (len(pairs) + 10.0)

    def rank(self, history):
        h = list(history)[-self.max_history:]
        rows = []
        for d in range(10):
            f10 = self._freq(h, d, 10)
            f25 = self._freq(h, d, 25)
            f50 = self._freq(h, d, 50)
            gap = self._gap(h, d)
            trans = self._transition(h, d)

            # Relative signal score for ranking only.
            score = (
                (0.10 - f10) * 16.0 +
                (0.10 - f25) * 10.0 +
                (0.10 - f50) * 6.0 +
                min(gap, 20) * 0.14 +
                (trans - 0.10) * 18.0
            )
            rows.append({"digit": d, "score": round(score, 6)})

        rows.sort(key=lambda x: x["score"], reverse=True)
        for i, row in enumerate(rows, 1):
            row["rank"] = i

        top4 = rows[:4]
        margin_1_2 = top4[0]["score"] - top4[1]["score"] if len(top4) > 1 else 0.0
        return {
            "ready": len(h) >= self.min_history,
            "history_count": len(h),
            "ranking": rows,
            "top4": top4,
            "top_margin": round(margin_1_2, 6),
        }
