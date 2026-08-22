#!/usr/bin/env python3
"""Controlled migration from quant_market_bot's legacy SQLite DB into v2.

The legacy DB is never modified. The destination DB is updated transactionally.
The script supports dry-run mode and keeps legacy model outputs in archival tables.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SCHEMA = Path(__file__).with_name("001_legacy_archive.sql")
DEFAULT_SOURCE = ROOT.parent / "quant_market_bot" / "data" / "market_data.db"
DEFAULT_DEST = ROOT / "data" / "database" / "market_data_v2.db"


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def get_map(conn: sqlite3.Connection, table: str, key_col: str, value_col: str) -> dict:
    return {
        row[0]: row[1]
        for row in conn.execute(
            f"SELECT {qident(key_col)}, {qident(value_col)} FROM {qident(table)}"
        )
    }


def migrate_assets(src: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    rows = src.execute(
        "SELECT ticker, empresa, sector, activo FROM universo_tickers"
    ).fetchall()
    for ticker, empresa, sector, activo in rows:
        dst.execute(
            """
            INSERT INTO assets(ticker, name, sector, active, source)
            VALUES (?, ?, ?, ?, 'legacy:universo_tickers')
            ON CONFLICT(ticker) DO UPDATE SET
                name=COALESCE(excluded.name, assets.name),
                sector=COALESCE(excluded.sector, assets.sector),
                active=excluded.active,
                updated_at=CURRENT_TIMESTAMP
            """,
            (ticker, empresa, sector, activo),
        )
    return len(rows)


def migrate_prices(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    source_label: str,
) -> int:
    asset_map = get_map(dst, "assets", "ticker", "asset_id")
    count = 0
    cur = src.execute(
        "SELECT ticker, timestamp, open, high, low, close, volume FROM precios"
    )
    batch = []
    for ticker, ts, op, hi, lo, close, volume in cur:
        asset_id = asset_map.get(ticker)
        if asset_id is None:
            continue
        batch.append((asset_id, ts, "1m", op, hi, lo, close, volume, source_label, 0))
        if len(batch) >= 5000:
            dst.executemany(
                """
                INSERT OR IGNORE INTO price_bars(
                    asset_id, timestamp, interval, open, high, low, close,
                    volume, source, is_adjusted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                batch,
            )
            count += len(batch)
            batch.clear()
    if batch:
        dst.executemany(
            """
            INSERT OR IGNORE INTO price_bars(
                asset_id, timestamp, interval, open, high, low, close,
                volume, source, is_adjusted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        count += len(batch)
    return count


def migrate_news(src: sqlite3.Connection, dst: sqlite3.Connection) -> tuple[int, int]:
    rows = src.execute(
        """
        SELECT id, ticker, timestamp, titulo, fuente, resumen
        FROM noticias
        """
    ).fetchall()
    asset_map = get_map(dst, "assets", "ticker", "asset_id")
    news_count = 0
    links = 0
    for news_id, ticker, ts, title, source, summary in rows:
        dst.execute(
            """
            INSERT OR IGNORE INTO news_documents(
                news_id, published_at, source_name, title, summary
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (news_id, ts, source, title, summary),
        )
        news_count += 1
        asset_id = asset_map.get(ticker)
        if asset_id is not None:
            dst.execute(
                """
                INSERT OR IGNORE INTO news_assets(news_id, asset_id, mention_strength, role)
                VALUES (?, ?, 1.0, 'legacy_primary_ticker')
                """,
                (news_id, asset_id),
            )
            links += 1
    return news_count, links


def migrate_events(src: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    rows = src.execute(
        "SELECT id, ticker, timestamp, id_evento FROM noticias WHERE id_evento IS NOT NULL"
    ).fetchall()
    asset_map = get_map(dst, "assets", "ticker", "asset_id")
    unique_events: set[str] = set()
    for news_id, ticker, ts, event_id in rows:
        if not event_id:
            continue
        unique_events.add(event_id)
        dst.execute(
            """
            INSERT OR IGNORE INTO events(
                event_id, event_type, canonical_title, first_seen_at, last_seen_at,
                event_scope, metadata_json
            ) VALUES (?, 'legacy_event', NULL, ?, ?, 'asset', ?)
            """,
            (event_id, ts, ts, json.dumps({'source': 'legacy'})),
        )
        dst.execute(
            """
            INSERT OR IGNORE INTO event_news(event_id, news_id, evidence_strength)
            VALUES (?, ?, 1.0)
            """,
            (event_id, news_id),
        )
        asset_id = asset_map.get(ticker)
        if asset_id is not None:
            dst.execute(
                """
                INSERT OR IGNORE INTO event_assets(
                    event_id, asset_id, relevance
                ) VALUES (?, ?, 1.0)
                """,
                (event_id, asset_id),
            )
    return len(unique_events)


def migrate_relations(src: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    rows = src.execute(
        "SELECT origen, destino, peso, ultima_actualizacion FROM relaciones_organicas"
    ).fetchall()
    entity_map = {}
    for ticker, in src.execute("SELECT ticker FROM universo_tickers"):
        entity_name = ticker
        dst.execute(
            """
            INSERT OR IGNORE INTO entities(entity_type, canonical_name, external_id)
            VALUES ('asset', ?, ?)
            """,
            (entity_name, ticker),
        )
        entity_map[ticker] = dst.execute(
            "SELECT entity_id FROM entities WHERE entity_type='asset' AND canonical_name=?",
            (entity_name,),
        ).fetchone()[0]
    count = 0
    for origin, dest, weight, updated_at in rows:
        if origin not in entity_map or dest not in entity_map:
            continue
        dst.execute(
            """
            INSERT OR IGNORE INTO entity_relations(
                source_entity_id, target_entity_id, relation_type,
                weight, confidence, source, valid_from,
                observed_count, last_validated_at, metadata_json
            ) VALUES (?, ?, 'learned_relation', ?, NULL,
                      'legacy:organic_cooccurrence', ?, ?, ?, ?)
            """,
            (
                entity_map[origin],
                entity_map[dest],
                float(weight) if weight is not None else None,
                updated_at,
                int(weight or 0),
                updated_at,
                json.dumps({'legacy_semantics': 'cooccurrence_count'}),
            ),
        )
        count += 1
    return count


def migrate_macro(src: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    rows = src.execute(
        "SELECT fecha, vix, tnx, petroleo, dolar FROM macro_diario"
    ).fetchall()
    inserted = 0
    for fecha, vix, tnx, oil, dolar in rows:
        values = {
            'VIX': (vix, 'index'),
            'TNX': (tnx, '%'),
            'WTI': (oil, 'price'),
            'DXY': (dolar, 'index'),
        }
        for symbol, (value, unit) in values.items():
            if value is None:
                continue
            dst.execute(
                """
                INSERT OR IGNORE INTO macro_observations(
                    symbol, observation_time, value, source, unit
                ) VALUES (?, ?, ?, 'legacy:macro_diario', ?)
                """,
                (symbol, fecha, value, unit),
            )
            inserted += 1
    return inserted


def archive_legacy_tables(src: sqlite3.Connection, dst: sqlite3.Connection) -> dict:
    stats: dict[str, int] = {}

    for table, columns, target in [
        (
            'correlaciones',
            ('id_noticia', 'ticker', 'es_contagio', 'sentimiento',
             'fiabilidad_fuente', 'divergencia_previa_pct', 'precio_instante',
             'precio_mfe_60m', 'impacto_mfe_60m_pct'),
            'legacy_correlations',
        ),
        (
            'vectores_estado',
            ('id_noticia', 'ticker', 'rsi', 'momentum_pct', 'atr', 'vix',
             'tnx', 'petroleo', 'dolar'),
            'legacy_state_vectors',
        ),
        (
            'paper_trading',
            ('id_operacion', 'id_noticia', 'ticker', 'fecha_senal', 'horizonte_horas',
             'rendimiento_esperado_pct', 'certeza_pct', 'precio_entrada',
             'precio_salida_real', 'rendimiento_real_pct'),
            'legacy_paper_trading',
        ),
    ]:
        if not table_exists(src, table):
            continue
        placeholders = ",".join("?" for _ in columns)
        rows = src.execute(
            f"SELECT {', '.join(qident(c) for c in columns)} FROM {qident(table)}"
        )
        insert_sql = (
            f"INSERT INTO {qident(target)} "
            f"({', '.join(qident(c) for c in columns)}, source_database) "
            f"VALUES ({placeholders}, ?)"
        )
        count = 0
        for row in rows:
            dst.execute(insert_sql, tuple(row) + (str(DEFAULT_SOURCE),))
            count += 1
        stats[target] = count
    return stats


def table_exists(src: sqlite3.Connection, name: str) -> bool:
    return src.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def migrate(source: Path, destination: Path, dry_run: bool = False) -> dict:
    if not source.exists():
        raise FileNotFoundError(source)
    if not destination.exists():
        raise FileNotFoundError(
            f"Destination DB inexistente: {destination}. Ejecutá primero database/init_db.py"
        )

    src = sqlite3.connect(source)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(destination)
    dst.execute("PRAGMA foreign_keys = ON")

    report: dict = {'source': str(source), 'destination': str(destination), 'dry_run': dry_run}
    try:
        archive_schema = ARCHIVE_SCHEMA.read_text(encoding='utf-8')
        dst.executescript(archive_schema)

        if dry_run:
            # Roll back archive tables too so dry-run leaves destination untouched.
            dst.rollback()
            report['message'] = 'Dry-run: no se escribieron datos.'
            report['source_counts'] = {
                table: src.execute(f'SELECT COUNT(*) FROM {qident(table)}').fetchone()[0]
                for table in (
                    'universo_tickers', 'precios', 'noticias', 'relaciones_organicas',
                    'macro_diario', 'correlaciones', 'vectores_estado', 'paper_trading'
                ) if table_exists(src, table)
            }
            return report

        dst.execute('BEGIN')
        report['assets'] = migrate_assets(src, dst)
        report['price_bars'] = migrate_prices(src, dst, 'legacy:yfinance')
        news_count, news_links = migrate_news(src, dst)
        report['news_documents'] = news_count
        report['news_asset_links'] = news_links
        report['events'] = migrate_events(src, dst)
        report['relations'] = migrate_relations(src, dst)
        report['macro_observations'] = migrate_macro(src, dst)
        report['legacy_archive'] = archive_legacy_tables(src, dst)

        dst.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('legacy_migration_source', ?)",
            (str(source),),
        )
        dst.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('legacy_migration_completed_at', CURRENT_TIMESTAMP)"
        )
        dst.commit()

        return report
    except Exception:
        dst.rollback()
        raise
    finally:
        src.close()
        dst.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--destination', type=Path, default=DEFAULT_DEST)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    report = migrate(args.source, args.destination, args.dry_run)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
