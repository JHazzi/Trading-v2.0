import copy
import json
import unittest
from pathlib import Path

from product.workbench_v0.state_contract import ContractError, load_state, validate_state


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "product" / "workbench_v0" / "sample_state.json"


class InvestmentStateV0Tests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(SAMPLE.read_text(encoding="utf-8"))

    def test_sample_is_valid(self):
        state = load_state(SAMPLE)
        self.assertEqual(state.payload["asset"]["ticker"], "AAPL")
        self.assertEqual(len(state.payload["forecasts"]), 3)
        self.assertEqual(len(state.sha256), 64)

    def test_rejects_non_monotonic_quantiles(self):
        payload = copy.deepcopy(self.payload)
        payload["forecasts"][0]["quantiles"]["q25"] = 9.0
        with self.assertRaises(ContractError):
            validate_state(payload)

    def test_rejects_non_monotonic_trajectory_quantiles(self):
        payload = copy.deepcopy(self.payload)
        payload["forecasts"][1]["trajectory"]["points"][0]["quantiles"]["q25"] = 99.0
        with self.assertRaises(ContractError):
            validate_state(payload)

    def test_rejects_confidence_outside_0_100(self):
        payload = copy.deepcopy(self.payload)
        payload["forecasts"][1]["confidence"]["points"][0]["score"] = 120
        with self.assertRaises(ContractError):
            validate_state(payload)

    def test_rejects_duplicate_trajectory_offsets(self):
        payload = copy.deepcopy(self.payload)
        payload["forecasts"][2]["trajectory"]["points"][1]["offset_sessions"] = 1
        with self.assertRaises(ContractError):
            validate_state(payload)


    def test_accepts_multi_resolution_temporal_contract(self):
        state = validate_state(copy.deepcopy(self.payload))
        temporal = state.payload["temporal_contract"]
        self.assertEqual(temporal["version"], "multi_resolution_time_v001")
        self.assertEqual(temporal["heads"][0]["kind"], "INTRADAY")

    def test_rejects_invalid_temporal_anchor(self):
        payload = copy.deepcopy(self.payload)
        payload["temporal_contract"]["evaluation_anchors"][0]["coordinate"]["offset_trading_minutes"] = -5
        with self.assertRaises(ContractError):
            validate_state(payload)

    def test_rejects_buy_signal_in_v0(self):
        payload = copy.deepcopy(self.payload)
        payload["decision"]["status"] = "BUY_CANDIDATE"
        with self.assertRaises(ContractError):
            validate_state(payload)

    def test_rejects_unknown_evidence_level(self):
        payload = copy.deepcopy(self.payload)
        payload["forecasts"][0]["evidence_level"] = "TRUST_ME"
        with self.assertRaises(ContractError):
            validate_state(payload)


if __name__ == "__main__":
    unittest.main()
