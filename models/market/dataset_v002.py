from pathlib import Path
import sqlite3, pandas as pd
FEATURES_V002=[
'atr_14_pct','realized_vol_30m_pct','realized_vol_60m_pct','realized_vol_390m_pct','trend_slope_30m_pct','trend_slope_60m_pct','trend_slope_390m_pct','return_5m_pct','return_15m_pct','return_30m_pct','return_60m_pct','return_390m_pct',
'relative_return_5m_market_pct','relative_return_15m_market_pct','relative_return_30m_market_pct','relative_return_60m_market_pct','relative_return_390m_market_pct',
'relative_return_5m_sector_pct','relative_return_15m_sector_pct','relative_return_30m_sector_pct','relative_return_60m_sector_pct','relative_return_390m_sector_pct',
'return_percentile_5m','return_percentile_15m','return_percentile_30m','return_percentile_60m','volatility_percentile_60m','volume_percentile_60m',
'market_return_5m_pct','market_return_15m_pct','market_return_30m_pct','market_return_60m_pct','market_return_390m_pct','market_vol_ratio_30m_390m','breadth_5m','breadth_15m','breadth_30m','breadth_60m']
TARGET='return_pct'
def load_v002(db_path:Path,horizon_seconds:int,min_coverage:float=95.0):
    with sqlite3.connect(db_path) as conn:
        sc=','.join('ms.'+c for c in FEATURES_V002)
        q=f'''SELECT ro.outcome_id,ro.asset_id,ro.origin_time,ro.horizon_seconds,ro.return_pct,ro.coverage_pct,{sc}
              FROM realized_outcomes ro JOIN market_state_v002_snapshots ms
              ON ms.asset_id=ro.asset_id AND ms.timestamp=ro.origin_time AND ms.feature_version='market_state_v0.2.0'
              WHERE ro.horizon_scope='intrasession' AND ro.coverage_pct>=? AND ro.horizon_seconds=?
              ORDER BY ro.origin_time,ro.asset_id'''
        return pd.read_sql_query(q,conn,params=(min_coverage,horizon_seconds))
