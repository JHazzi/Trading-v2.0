from datetime import datetime, timezone

import pandas as pd

from ingestion.market_reference.cboe_vix_daily_v001 import vix_availability
from features.market.daily_v0052_financial_conditions import (
    filter_vix_to_equity_sessions,
    total_return_from_close_and_cash,
)


def test_non_equity_provider_row_is_not_given_equity_clock():
    retrieved = "2026-08-25T20:00:00+00:00"
    closes = {
        "2022-05-27": datetime(2022, 5, 27, 20, 0, tzinfo=timezone.utc),
        "2022-05-31": datetime(2022, 5, 31, 20, 0, tzinfo=timezone.utc),
    }
    available_at, basis, eligible = vix_availability(
        "2022-05-30", closes, retrieved, "normal"
    )
    assert eligible is False
    assert available_at == retrieved
    assert basis == (
        "provider_non_equity_session_retrieval_only_not_model_eligible"
    )


def test_equity_session_gets_close_plus_15m():
    retrieved = "2026-08-25T20:00:00+00:00"
    closes = {
        "2022-05-27": datetime(2022, 5, 27, 20, 0, tzinfo=timezone.utc),
    }
    available_at, basis, eligible = vix_availability(
        "2022-05-27", closes, retrieved, "normal"
    )
    assert eligible is True
    assert available_at == "2022-05-27T20:15:00+00:00"
    assert basis == "normal"


def test_memorial_day_provider_row_is_removed_before_lag():
    frame = pd.DataFrame({
        "trading_day": ["2022-05-27", "2022-05-30", "2022-05-31"],
        "close": [25.72, 99.99, 26.19],
    })
    eligible = filter_vix_to_equity_sessions(frame)
    assert eligible["trading_day"].tolist() == [
        "2022-05-27", "2022-05-31"
    ]
    eligible["lag1"] = eligible["close"].shift(1)
    may31_lag = eligible.loc[
        eligible["trading_day"] == "2022-05-31", "lag1"
    ].iloc[0]
    assert may31_lag == 25.72
    assert may31_lag != 99.99


def test_cash_distribution_return_logic_is_preserved():
    close = pd.Series([100.0, 99.0])
    cash = pd.Series([0.0, 1.0])
    result = total_return_from_close_and_cash(close, cash)
    assert abs(result.iloc[1]) < 1e-12
