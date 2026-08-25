import json
from pathlib import Path
import numpy as np, pandas as pd
from features.market.daily_v005_external_state import _returns,_drawdown

def test_returns_are_past_only():
    s=pd.Series([100.,101.,103.,102.])
    r=_returns(s,1)
    assert np.isnan(r.iloc[0])
    assert abs(r.iloc[2]-(103/101-1)*100)<1e-12

def test_drawdown():
    s=pd.Series([100.,110.,105.])
    d=_drawdown(s,2)
    assert abs(d.iloc[2]-(105/110-1)*100)<1e-12

def test_config_stage_is_incremental():
    c=json.loads(Path("config/market_brain_daily_v005_external_state.json").read_text())
    assert c["stages"]["market_tradables"]["enabled"] is True
    assert c["stages"]["sector_etfs"]["enabled"] is False
    assert c["stages"]["risk_rates_credit"]["enabled"] is False
    assert c["stages"]["macro_vintages"]["enabled"] is False

def test_no_events_in_external_state_builder():
    x=Path("features/market/daily_v005_external_state.py").read_text()
    assert "news_" not in x
    assert "event_" not in x

def test_historical_pit_is_not_overclaimed():
    c=json.loads(Path("config/market_brain_daily_v005_external_state.json").read_text())
    assert c["strict_historical_pit"] is False
    assert "not_strict_pit" in c["historical_availability_basis"]
