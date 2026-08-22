import argparse,json,pickle
from pathlib import Path
import numpy as np
from sklearn.metrics import mean_absolute_error,root_mean_squared_error
from evaluation.backtest.global_time_split import global_time_split
from models.market.dataset import load_supervised_dataset,FEATURES as FEATURES_V001,TARGET
from models.market.dataset_v002 import load_v002,FEATURES_V002
def score(y,p): return {'n':len(y),'mae_pct':float(mean_absolute_error(y,p)),'rmse_pct':float(root_mean_squared_error(y,p)),'directional_accuracy':float(np.mean(np.sign(y)==np.sign(p)))}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--horizon',type=int,required=True); ap.add_argument('--v001-artifact',required=True); ap.add_argument('--v002-artifact',required=True); ap.add_argument('--db',default='data/database/market_data_v2.db'); a=ap.parse_args()
    d1=load_supervised_dataset(Path(a.db),a.horizon); d2=load_v002(Path(a.db),a.horizon); s1=global_time_split(d1); s2=global_time_split(d2)
    if s1.cutoff!=s2.cutoff: raise SystemExit(f'Cutoffs distintos: {s1.cutoff} vs {s2.cutoff}')
    with open(a.v001_artifact,'rb') as f:m1=pickle.load(f)['model']
    with open(a.v002_artifact,'rb') as f:m2=pickle.load(f)['model']
    y1=s1.test[TARGET].to_numpy(); y2=s2.test[TARGET].to_numpy(); r1=score(y1,m1.predict(s1.test[FEATURES_V001])); r2=score(y2,m2.predict(s2.test[FEATURES_V002])); print(json.dumps({'horizon_seconds':a.horizon,'cutoff':s1.cutoff.isoformat(),'v001':r1,'v002':r2,'mae_improvement_v002_vs_v001_pct':1-r2['mae_pct']/r1['mae_pct']},indent=2))
if __name__=='__main__': main()
