import csv
import os
from datetime import datetime, timezone
from . import config


def log_gap(question: str, best_score: float):
    os.makedirs(os.path.dirname(config.GAPS_LOG), exist_ok=True)
    file_exists = os.path.exists(config.GAPS_LOG)
    with open(config.GAPS_LOG, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "question", "best_similarity_score"])
        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            question,
            f"{best_score:.4f}",
        ])
