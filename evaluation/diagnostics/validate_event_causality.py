from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REQUIRED = [
    "event_clusters",
    "event_cluster_news",
    "event_evidence",
    "event_states",
    "event_reaction_outcomes",
    "event_source_knowledge",
]


def validate(db: Path) -> dict:
    with sqlite3.connect(db) as conn:
        tables = {
            table: conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            is not None
            for table in REQUIRED
        }
        missing = [name for name, exists in tables.items() if not exists]

        result = {
            "db": str(db),
            "tables": tables,
            "missing_tables": missing,
            "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "news": conn.execute(
                "SELECT COUNT(*) FROM news_documents"
            ).fetchone()[0],
        }

        if missing:
            result.update(
                {
                    "status": "INCOMPLETE",
                    "feature_smoke_test_ready": False,
                    "training_ready": False,
                    "blocking_reasons": [
                        "missing_event_layer_tables",
                    ],
                }
            )
            return result

        counts = {
            "event_clusters": conn.execute(
                "SELECT COUNT(*) FROM event_clusters"
            ).fetchone()[0],
            "event_evidence": conn.execute(
                "SELECT COUNT(*) FROM event_evidence"
            ).fetchone()[0],
            "event_states": conn.execute(
                "SELECT COUNT(*) FROM event_states"
            ).fetchone()[0],
            "event_reaction_outcomes": conn.execute(
                "SELECT COUNT(*) FROM event_reaction_outcomes"
            ).fetchone()[0],
        }
        causal_violations = conn.execute(
            """
            SELECT COUNT(*)
            FROM event_states
            WHERE available_at > state_time
            """
        ).fetchone()[0]

        blockers = []
        if counts["event_states"] == 0:
            blockers.append("event_states_empty")
        if counts["event_evidence"] == 0:
            blockers.append("event_evidence_empty")
        if counts["event_reaction_outcomes"] == 0:
            blockers.append("event_reaction_outcomes_empty")
        if causal_violations:
            blockers.append("causal_violations")

        feature_ready = (
            counts["event_states"] > 0 and causal_violations == 0
        )
        training_ready = (
            feature_ready
            and counts["event_evidence"] > 0
            and counts["event_reaction_outcomes"] > 0
        )

        if causal_violations:
            status = "FAIL"
        elif not feature_ready:
            status = "INCOMPLETE"
        else:
            status = "PASS"

        result.update(
            {
                "counts": counts,
                "causal_violations": causal_violations,
                "status": status,
                "feature_smoke_test_ready": feature_ready,
                "training_ready": training_ready,
                "blocking_reasons": blockers,
            }
        )
        return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/database/market_data_v2.db")
    args = ap.parse_args()
    print(json.dumps(validate(Path(args.db)), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
