from __future__ import annotations
import argparse, json, sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import exchange_calendars as xcals
except ImportError as exc:
    raise SystemExit(
        "Falta exchange_calendars. Instalar con: pip install exchange-calendars"
    ) from exc

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data/database/market_data_v2.db"
NY = ZoneInfo("America/New_York")

def iso_z(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()

def build_sessions(conn, exchange="XNYS"):
    cal = xcals.get_calendar(exchange)
    rows = conn.execute("""
        SELECT MIN(timestamp), MAX(timestamp)
        FROM price_bars
        WHERE timestamp IS NOT NULL
    """).fetchone()
    if not rows or not rows[0] or not rows[1]:
        return 0

    start = datetime.fromisoformat(rows[0].replace("Z","+00:00")).date()
    end = datetime.fromisoformat(rows[1].replace("Z","+00:00")).date()

    schedule = cal.schedule.loc[str(start):str(end)]
    count=0
    for day, r in schedule.iterrows():
        open_utc=r["open"].to_pydatetime()
        close_utc=r["close"].to_pydatetime()
        day_str=day.date().isoformat()
        session_id=f"{exchange}:regular:{day_str}"
        conn.execute("""
            INSERT INTO market_sessions
            (session_id,trading_day,exchange,session_type,open_time,close_time)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
              open_time=excluded.open_time,
              close_time=excluded.close_time
        """,(session_id,day_str,exchange,"regular",iso_z(open_utc),iso_z(close_utc)))
        count += 1
    return count

def attach_bars(conn, exchange="XNYS"):
    cal=xcals.get_calendar(exchange)
    cur=conn.execute("""
        SELECT price_bar_id, timestamp
        FROM price_bars
        WHERE session_id IS NULL
        ORDER BY timestamp
    """)
    rows=cur.fetchall()
    updated=0
    cache={}
    for price_bar_id, ts in rows:
        dt=datetime.fromisoformat(ts.replace("Z","+00:00"))
        local=dt.astimezone(NY)
        day=local.date().isoformat()
        if day not in cache:
            try:
                sched=cal.schedule.loc[day:day]
            except Exception:
                sched=None
            if sched is None or len(sched)==0:
                cache[day]=None
            else:
                r=sched.iloc[0]
                o=r["open"].to_pydatetime()
                c=r["close"].to_pydatetime()
                cache[day]=(o.astimezone(timezone.utc),c.astimezone(timezone.utc))
        session=cache[day]
        if not session:
            continue
        o,c=session
        if o <= dt < c:
            sid=f"{exchange}:regular:{day}"
            conn.execute("""
                UPDATE price_bars
                SET session_id=?, trading_day=?
                WHERE price_bar_id=?
            """,(sid,day,price_bar_id))
            updated+=1
    return updated

def run(db=DB, exchange="XNYS"):
    conn=sqlite3.connect(db)
    try:
        sessions=build_sessions(conn,exchange)
        updated=attach_bars(conn,exchange)
        conn.commit()
        unassigned=conn.execute(
            "SELECT COUNT(*) FROM price_bars WHERE session_id IS NULL"
        ).fetchone()[0]
        print(json.dumps({
            "exchange":exchange,
            "sessions_created_or_updated":sessions,
            "bars_sessionized":updated,
            "bars_unassigned":unassigned
        },indent=2))
    finally:
        conn.close()

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",type=Path,default=DB)
    ap.add_argument("--exchange",default="XNYS")
    args=ap.parse_args()
    run(args.db,args.exchange)
