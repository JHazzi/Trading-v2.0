from __future__ import annotations
import argparse, sqlite3, json
from pathlib import Path
import numpy as np
import pandas as pd
FEATURE_VERSION='market_state_v0.2.0'

def build(max_assets=None):
    db=Path('data/database/market_data_v2.db')
    with sqlite3.connect(db) as conn:
        q = '''
        SELECT
            p.asset_id,
            p.timestamp,
            p.close,
            p.volume,
            a.sector
        FROM price_bars p
        JOIN assets a
            ON a.asset_id = p.asset_id
        JOIN asset_universe_membership um
            ON um.asset_id = p.asset_id
        AND um.universe = 'price_observed'
        AND p.timestamp >= um.valid_from
        AND (
            um.valid_to IS NULL
            OR p.timestamp <= um.valid_to
        )
        WHERE p.interval = '1m'
        '''
        params=()
        if max_assets is not None:
            ids = [
                r[0]
                for r in conn.execute(
                    '''
                    SELECT DISTINCT asset_id
                    FROM asset_universe_membership
                    WHERE universe = 'price_observed'
                    ORDER BY asset_id
                    LIMIT ?
                    ''',
                    (max_assets,)
                )
            ]
            if not ids: return {'rows':0}
            ph=','.join('?'*len(ids)); q+=f' AND p.asset_id IN ({ph})'; params=tuple(ids)
        df=pd.read_sql_query(q,conn,params=params)
        if df.empty: return {'rows':0,'feature_version':FEATURE_VERSION}
        df['timestamp']=pd.to_datetime(df['timestamp'],utc=True)
        df=df.sort_values(['asset_id','timestamp'])
        g=df.groupby('asset_id',sort=False)
        for h,n in [('5m',5),('15m',15),('30m',30),('60m',60),('390m',390)]:
            df[f'return_{h}_pct']=g['close'].pct_change(n)*100
        logret=g['close'].transform(lambda s: np.log(s).diff())
        for h,n in [('30m',30),('60m',60),('390m',390)]:
            df[f'realized_vol_{h}_pct']=logret.groupby(df.asset_id).transform(lambda s:s.rolling(n,min_periods=max(5,n//3)).std())*np.sqrt(n)*100
        df['atr_14_pct']=g['close'].transform(lambda s:s.diff().abs().rolling(14,min_periods=5).mean())/df['close']*100
        for h,n in [('30m',30),('60m',60),('390m',390)]:
            df[f'trend_slope_{h}_pct']=g['close'].transform(lambda s:s.rolling(n,min_periods=max(10,n//3)).apply(lambda x: np.polyfit(np.arange(len(x)),x,1)[0]/x[-1]*100 if len(x)>1 and x[-1] else np.nan,raw=True))
        volma=g['volume'].transform(lambda s:s.rolling(60,min_periods=10).mean())
        df['relative_volume_60m']=df['volume']/volma.replace(0,np.nan)
        def rank(s): return s.rank(pct=True,method='average')
        for h in ['5m','15m','30m','60m','390m']:
            c=f'return_{h}_pct'
            df[f'market_return_{h}_pct']=df.groupby('timestamp')[c].transform('mean')
            df[f'return_percentile_{h}']=df.groupby('timestamp')[c].transform(rank)
            sector_mean=df.groupby(['timestamp','sector'],dropna=False)[c].transform('mean')
            df[f'relative_return_{h}_market_pct']=df[c]-df[f'market_return_{h}_pct']
            df[f'relative_return_{h}_sector_pct']=df[c]-sector_mean
        df['volatility_percentile_60m']=df.groupby('timestamp')['realized_vol_60m_pct'].transform(rank)
        df['volume_percentile_60m']=df.groupby('timestamp')['relative_volume_60m'].transform(rank)
        for h in ['5m','15m','30m','60m']:
            df[f'breadth_{h}']=df.groupby('timestamp')[f'return_{h}_pct'].transform(lambda s:(s>0).mean())
        df['market_vol_ratio_30m_390m']=df.groupby('timestamp')['realized_vol_30m_pct'].transform('mean')/df.groupby('timestamp')['realized_vol_390m_pct'].transform('mean').replace(0,np.nan)
        df=df.dropna(subset=['return_390m_pct','realized_vol_390m_pct','trend_slope_390m_pct'])
        cols=['asset_id','timestamp','close'] + [c for c in df.columns if c.startswith(('atr_','realized_vol_','trend_slope_','return_','relative_return_','volatility_percentile_','volume_percentile_','market_return_','market_vol_ratio_','breadth_'))] + ['sector']
        cols=list(dict.fromkeys(cols))
        feature_cols=[c for c in cols if c not in ('asset_id','timestamp','close','sector')]
        sql=f'''INSERT INTO market_state_v002_snapshots(asset_id,timestamp,last_price,{','.join(feature_cols)},sector,feature_version)
                VALUES ({','.join(['?']*(len(feature_cols)+5))})
                ON CONFLICT(asset_id,timestamp,feature_version) DO UPDATE SET last_price=excluded.last_price,{','.join(f'{c}=excluded.{c}' for c in feature_cols)},sector=excluded.sector'''
        df["timestamp"] = (
        pd.to_datetime(df["timestamp"], utc=True)
        .dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        )
        rows=[]
        for r in df[['asset_id','timestamp','close',*feature_cols,'sector']].itertuples(index=False,name=None):
            rows.append((*r,'market_state_v0.2.0'))
        conn.executemany(sql,rows); conn.commit()
        return {'rows':len(rows),'assets':int(df.asset_id.nunique()),'feature_version':FEATURE_VERSION}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--max-assets',type=int); ap.add_argument('--json',action='store_true'); a=ap.parse_args(); out=build(a.max_assets); print(json.dumps(out,indent=2) if a.json else out)
if __name__=='__main__': main()
