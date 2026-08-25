from pathlib import Path

import models.events.dataset_v002 as dataset_v002
import models.events.train_v002 as frozen_v002
from models.events.train_v0031_deep import (
    DATASET_CONTRACT,
    EVENT_FEATURE_VERSION,
    LABEL_VERSION,
    MODEL_VERSION,
    _install_deep_contract,
)


def test_deep_contract_is_explicit():
    assert EVENT_FEATURE_VERSION == "event_state_v0031_deep"
    assert LABEL_VERSION == "event_reaction_daily_v0031_deep"
    assert MODEL_VERSION == "event_brain_v002_architecture_on_deep_v0031"
    assert DATASET_CONTRACT == "deep_sec_2016_2026_research_reconstruction_v0031"


def test_frozen_dataset_defaults_remain_pilot_versions():
    assert dataset_v002.EVENT_FEATURE_VERSION == "event_state_v002"
    assert dataset_v002.LABEL_VERSION == "event_reaction_daily_v002"


def test_runner_rebinds_only_training_contract_globals():
    original_fold_fn = frozen_v002._fold_predictions
    original_metric_fn = frozen_v002._metrics
    _install_deep_contract()
    assert frozen_v002.EVENT_FEATURE_VERSION == EVENT_FEATURE_VERSION
    assert frozen_v002.LABEL_VERSION == LABEL_VERSION
    assert frozen_v002.MODEL_VERSION == MODEL_VERSION
    assert frozen_v002._fold_predictions is original_fold_fn
    assert frozen_v002._metrics is original_metric_fn


def test_load_dataset_versions_are_passed_explicitly():
    source = Path("models/events/train_v0031_deep.py").read_text()
    assert "event_feature_version=EVENT_FEATURE_VERSION" in source
    assert "label_version=LABEL_VERSION" in source


def test_reports_are_isolated_from_pilot():
    source = Path("models/events/train_v0031_deep.py").read_text()
    assert '"event_brain_v0031_deep"' in source
    assert '"deep_v0031"' in source


def test_strict_pit_is_not_claimed():
    source = Path("models/events/train_v0031_deep.py").read_text()
    assert '"strict_pit_event_evidence": False' in source
