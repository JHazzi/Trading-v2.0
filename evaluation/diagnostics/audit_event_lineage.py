from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def run(db: Path) -> dict:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row

        def scalar(sql: str, params: tuple = ()) -> int:
            return int(conn.execute(sql, params).fetchone()[0])

        event_count = scalar("SELECT COUNT(*) FROM events")
        linked_events = scalar(
            "SELECT COUNT(DISTINCT event_id) FROM event_news"
        )
        unlinked_events = scalar(
            """
            SELECT COUNT(*)
            FROM events e
            WHERE NOT EXISTS (
                SELECT 1
                FROM event_news en
                WHERE en.event_id = e.event_id
            )
            """
        )
        linked_news = scalar(
            "SELECT COUNT(DISTINCT news_id) FROM event_news"
        )
        news_total = scalar("SELECT COUNT(*) FROM news_documents")
        multi_source_events = scalar(
            """
            SELECT COUNT(*)
            FROM (
                SELECT en.event_id
                FROM event_news en
                JOIN news_documents n ON n.news_id = en.news_id
                GROUP BY en.event_id
                HAVING COUNT(DISTINCT COALESCE(n.source_name, '')) >= 2
            )
            """
        )
        events_with_time = scalar(
            "SELECT COUNT(*) FROM events WHERE event_time IS NOT NULL"
        )
        events_with_future_time = scalar(
            """
            SELECT COUNT(*)
            FROM events
            WHERE event_time IS NOT NULL
              AND event_time > COALESCE(first_seen_at, event_time)
            """
        )
        evidence_rows = scalar("SELECT COUNT(*) FROM event_evidence")
        state_rows = scalar("SELECT COUNT(*) FROM event_states")
        cluster_rows = scalar("SELECT COUNT(*) FROM event_clusters")
        reaction_rows = scalar(
            "SELECT COUNT(*) FROM event_reaction_outcomes"
        )
        orphan_event_news = scalar(
            """
            SELECT COUNT(*)
            FROM event_news en
            WHERE NOT EXISTS (
                SELECT 1 FROM events e
                WHERE e.event_id = en.event_id
            )
            OR NOT EXISTS (
                SELECT 1 FROM news_documents n
                WHERE n.news_id = en.news_id
            )
            """
        )
        singleton_events = scalar(
            """
            SELECT COUNT(*)
            FROM (
                SELECT event_id
                FROM event_news
                GROUP BY event_id
                HAVING COUNT(*) = 1
            )
            """
        )

        distribution = [
            dict(row)
            for row in conn.execute(
                """
                SELECT article_count, COUNT(*) AS event_count
                FROM (
                    SELECT event_id, COUNT(*) AS article_count
                    FROM event_news
                    GROUP BY event_id
                )
                GROUP BY article_count
                ORDER BY article_count
                LIMIT 25
                """
            )
        ]

        top_events = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    e.event_id,
                    e.event_type,
                    e.canonical_title,
                    COUNT(en.news_id) AS article_count,
                    e.first_seen_at,
                    e.last_seen_at,
                    e.event_time
                FROM events e
                LEFT JOIN event_news en ON en.event_id = e.event_id
                GROUP BY e.event_id
                ORDER BY article_count DESC
                LIMIT 20
                """
            )
        ]

        integrity_issues = []
        if orphan_event_news:
            integrity_issues.append("orphan_event_news")
        if unlinked_events:
            integrity_issues.append("events_without_news")

        readiness_blockers = []
        if cluster_rows == 0:
            readiness_blockers.append("event_clusters_empty")
        if evidence_rows == 0:
            readiness_blockers.append("event_evidence_empty")
        if state_rows == 0:
            readiness_blockers.append("event_states_empty")
        if reaction_rows == 0:
            readiness_blockers.append("event_reaction_outcomes_empty")
        if events_with_time == 0:
            readiness_blockers.append("event_time_missing_for_all_events")
        if linked_news < news_total:
            readiness_blockers.append("news_lineage_incomplete")

        integrity_status = "PASS" if not integrity_issues else "FAIL"
        training_readiness = (
            "READY" if not readiness_blockers and not integrity_issues
            else "BLOCKED"
        )

        return {
            "db": str(db),
            "events": {
                "total": event_count,
                "linked_to_news": linked_events,
                "unlinked": unlinked_events,
                "with_event_time": events_with_time,
                "event_time_after_first_seen": events_with_future_time,
                "with_multiple_source_evidence": multi_source_events,
            },
            "news": {
                "total": news_total,
                "linked_to_events": linked_news,
                "unlinked": news_total - linked_news,
            },
            "event_layer": {
                "clusters": cluster_rows,
                "evidence_rows": evidence_rows,
                "state_rows": state_rows,
                "reaction_rows": reaction_rows,
                "orphan_event_news": orphan_event_news,
            },
            "coverage": {
                "news_linked_ratio": _ratio(linked_news, news_total),
                "events_linked_ratio": _ratio(linked_events, event_count),
                "single_article_event_ratio": _ratio(
                    singleton_events, linked_events
                ),
                "multi_source_event_ratio": _ratio(
                    multi_source_events, linked_events
                ),
                "event_time_ratio": _ratio(
                    events_with_time, event_count
                ),
            },
            "article_count_distribution": distribution,
            "top_events_by_article_count": top_events,
            "integrity_status": integrity_status,
            "integrity_issues": integrity_issues,
            "training_readiness": training_readiness,
            "readiness_blockers": readiness_blockers,
            "status": (
                "PASS"
                if integrity_status == "PASS"
                and training_readiness == "READY"
                else "REVIEW"
            ),
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/database/market_data_v2.db")
    ap.add_argument("--output")
    args = ap.parse_args()

    result = run(Path(args.db))
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Reporte guardado en: {path}")


if __name__ == "__main__":
    main()
