from __future__ import annotations

VERSION = "market_brain_daily_v004_factorization_foundation_v001"

CONTRACT = {
    "version": VERSION,
    "motivation": (
        "V003 pooled absolute-return benchmark failed at H1/H3/H5/H10, "
        "with the dominant degradation appearing when day-level "
        "cross-sectional context was repeated on asset-day rows."
    ),
    "levels": {
        "market": {
            "unit": "origin_trading_day",
            "target": (
                "equal-weight mean future return across usable assets"
            ),
            "purpose": "learn common market component once per day",
        },
        "sector": {
            "unit": "origin_trading_day_x_sector",
            "target": (
                "equal-weight sector future return minus market factor"
            ),
            "purpose": "learn sector residual once per sector-day",
        },
        "asset": {
            "unit": "origin_trading_day_x_asset",
            "target": (
                "asset future return minus equal-weight sector future return"
            ),
            "purpose": "learn idiosyncratic / relative asset residual",
        },
    },
    "identity": (
        "asset_return = market_factor + sector_factor + asset_residual"
    ),
    "feature_routing": {
        "market_context": (
            "cross_section aggregate features excluding asset-minus-market"
        ),
        "sector_context": (
            "sector aggregate features excluding asset-minus-sector"
        ),
        "asset_relative": (
            "own features plus asset-minus-market and asset-minus-sector"
        ),
    },
    "scientific_guards": {
        "same_horizons": [1, 3, 5, 10],
        "future_information_in_features": False,
        "corporate_action_label_policy": "inherit_v003_usable_labels",
        "current_cohort_survivorship_free": False,
        "historical_price_pit_verified": False,
        "external_proxies": False,
        "macro": False,
        "events": False,
        "distributional_training": False,
    },
    "next_gate": (
        "Quantify target factorization and feature topology before "
        "materializing or training V004."
    ),
}
