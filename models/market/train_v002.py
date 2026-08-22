from pathlib import Path
import argparse,pickle
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from evaluation.backtest.global_time_split import global_time_split
from models.market.dataset_v002 import load_v002,FEATURES_V002,TARGET
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--horizon',type=int,required=True); ap.add_argument('--artifact'); ap.add_argument('--db',default='data/database/market_data_v2.db'); a=ap.parse_args()
    out=Path(a.artifact or f'models/market/artifacts/market_v002_{a.horizon}.pkl'); out.parent.mkdir(parents=True,exist_ok=True)
    df=load_v002(Path(a.db),a.horizon); s=global_time_split(df)
    model=make_pipeline(SimpleImputer(strategy='median'),RandomForestRegressor(n_estimators=250,max_depth=12,min_samples_leaf=50,random_state=42,n_jobs=-1))
    model.fit(s.train[FEATURES_V002],s.train[TARGET])
    payload={'model_version':'market_v002','horizon_seconds':a.horizon,'feature_version':'market_state_v0.2.0','features':FEATURES_V002,'target':TARGET,'model':model,'rows_total':len(df),'rows_train':len(s.train),'rows_test':len(s.test),'cutoff':s.cutoff.isoformat()}
    with out.open('wb') as f: pickle.dump(payload,f,pickle.HIGHEST_PROTOCOL)
    print({'artifact':str(out),'rows_total':len(df),'rows_train':len(s.train),'rows_test':len(s.test),'feature_count':len(FEATURES_V002)})
if __name__=='__main__': main()
