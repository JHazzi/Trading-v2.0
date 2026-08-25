from __future__ import annotations

from dataclasses import dataclass

FEATURE_VERSION = "market_daily_state_v003_foundation"
DATASET_CONTRACT = "market_daily_v003_all_asset_days_current_cohort_research"
TARGET_VERSION = "market_daily_reaction_v003"

HORIZONS = (1, 3, 5, 10)

# Enough history for the longest planned state feature (252-session drawdown)
# plus the current observation.
MIN_OWN_HISTORY_DAYS = 253
MIN_CROSS_SECTION_ASSETS = 50
MIN_SECTOR_PEERS_EX_TARGET = 3

PLANNED_ASSET_FEATURES = (
    "return_1",
    "return_3",
    "return_5",
    "return_10",
    "return_20",
    "return_63",
    "vol_5",
    "vol_20",
    "vol_63",
    "range_1",
    "volume_ratio_20",
    "drawdown_20",
    "drawdown_63",
    "drawdown_252",
)

PLANNED_CROSS_SECTION_FEATURES = (
    "market_equal_weight_return_1_loo",
    "market_equal_weight_return_5_loo",
    "market_median_return_1_loo",
    "market_breadth_positive_1_loo",
    "market_cross_section_vol_1_loo",
)

PLANNED_SECTOR_FEATURES = (
    "sector_equal_weight_return_1_loo",
    "sector_equal_weight_return_5_loo",
    "sector_breadth_positive_1_loo",
    "sector_peer_count",
    "sector_context_missing",
)

# External context is deliberately not required until the foundation audit
# proves that the corresponding series exists with a causal/as-of contract.
DESIRED_MARKET_PROXIES = (
    "SPY",
    "QQQ",
    "IWM",
)

DESIRED_SECTOR_PROXIES = (
    "XLK",
    "XLF",
    "XLE",
    "XLV",
    "XLY",
    "XLP",
    "XLI",
    "XLB",
    "XLU",
    "XLRE",
    "XLC",
)

DESIRED_VOLATILITY_PROXIES = (
    "^VIX",
    "VIX",
)

DESIRED_RATE_CREDIT_PROXIES = (
    "TLT",
    "IEF",
    "SHY",
    "HYG",
    "LQD",
)


@dataclass(frozen=True)
class DailyV003Contract:
    feature_version: str = FEATURE_VERSION
    dataset_contract: str = DATASET_CONTRACT
    target_version: str = TARGET_VERSION
    state_clock: str = "exchange_session_close"
    target_definition: str = "raw_close_t_to_raw_close_t_plus_h"
    adjusted_close_role: str = "audit_only_not_feature_identity"
    corporate_action_policy: str = "exclude_target_horizon_overlap"
    asset_selection: str = "current_asset_cohort_research_not_historical_membership"
    dynamic_entry_rule: str = f"own_quality_gated_days>={MIN_OWN_HISTORY_DAYS}"
    cross_section_rule: str = "eligible_assets_available_by_state_time_target_excluded"
    sector_rule: str = (
        "eligible_same_sector_available_by_state_time_target_excluded;"
        f"minimum_peers={MIN_SECTOR_PEERS_EX_TARGET};otherwise_missing_indicator"
    )
    event_join_rule: str = (
        "event_state_may_use_only_latest_market_prediction_with_"
        "market_state_time<=event_state_time"
    )
    strict_pit_prices: bool = False
    macro_enabled: bool = False


def as_dict() -> dict[str, object]:
    c = DailyV003Contract()
    return {
        **c.__dict__,
        "horizons": list(HORIZONS),
        "planned_asset_features": list(PLANNED_ASSET_FEATURES),
        "planned_cross_section_features": list(PLANNED_CROSS_SECTION_FEATURES),
        "planned_sector_features": list(PLANNED_SECTOR_FEATURES),
        "desired_market_proxies": list(DESIRED_MARKET_PROXIES),
        "desired_sector_proxies": list(DESIRED_SECTOR_PROXIES),
        "desired_volatility_proxies": list(DESIRED_VOLATILITY_PROXIES),
        "desired_rate_credit_proxies": list(DESIRED_RATE_CREDIT_PROXIES),
    }
