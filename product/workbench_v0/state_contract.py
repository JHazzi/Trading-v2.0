from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "investment_state_v0"
EVIDENCE_LEVELS = {
    "UNAVAILABLE",
    "RESEARCH_ONLY",
    "DEVELOPMENTAL",
    "PROSPECTIVE_PENDING",
    "PROSPECTIVE_SUPPORTED",
    "PRODUCTION_CANDIDATE",
}
V0_DECISION_STATUSES = {"INSUFFICIENT_EVIDENCE", "WATCH", "RISK_ALERT"}
QUANTILE_KEYS = ("q05", "q25", "q50", "q75", "q95")
HISTORY_MODES = {"PRICE", "NORMALIZED_INDEX"}
TRAJECTORY_SOURCE_KINDS = {"MODEL_OUTPUT", "ILLUSTRATIVE", "RESEARCH_RECONSTRUCTION"}
TEMPORAL_CONTRACT_VERSION = "multi_resolution_time_v001"
TIME_COORDINATE_KINDS = {
    "TRADING_MINUTES",
    "SESSION_CLOSE",
    "SESSION_OPEN",
    "WALL_CLOCK",
}
SESSION_PHASES = {
    "PRE_MARKET",
    "REGULAR",
    "POST_MARKET",
    "OVERNIGHT",
    "SESSION_OPEN",
    "SESSION_CLOSE",
    "UNKNOWN",
}
HEAD_KINDS = {"INTRADAY", "DAILY", "LONG_HORIZON"}


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedState:
    payload: dict[str, Any]
    sha256: str


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def state_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _require_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ContractError(f"{key} must be an object")
    return value


def _validate_evidence(value: Any, where: str) -> None:
    if value not in EVIDENCE_LEVELS:
        raise ContractError(f"{where}.evidence_level invalid: {value!r}")


def _numeric_quantiles(quantiles: Any, where: str) -> list[float]:
    if not isinstance(quantiles, dict):
        raise ContractError(f"{where}.quantiles must be an object")
    try:
        values = [float(quantiles[k]) for k in QUANTILE_KEYS]
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(f"{where}.quantiles must contain numeric {', '.join(QUANTILE_KEYS)}") from exc
    if values != sorted(values):
        raise ContractError(f"{where} quantiles are not monotonic")
    return values


def _validate_time_coordinate(value: Any, where: str) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be an object")
    kind = value.get("kind")
    if kind not in TIME_COORDINATE_KINDS:
        raise ContractError(f"{where}.kind invalid: {kind!r}")

    if kind == "TRADING_MINUTES":
        try:
            minutes = int(value.get("offset_trading_minutes"))
        except (TypeError, ValueError) as exc:
            raise ContractError(f"{where}.offset_trading_minutes must be an integer") from exc
        if minutes < 0:
            raise ContractError(f"{where}.offset_trading_minutes must be >= 0")
    elif kind in {"SESSION_CLOSE", "SESSION_OPEN"}:
        try:
            sessions = int(value.get("offset_sessions"))
        except (TypeError, ValueError) as exc:
            raise ContractError(f"{where}.offset_sessions must be an integer") from exc
        if sessions < 0:
            raise ContractError(f"{where}.offset_sessions must be >= 0")
    elif kind == "WALL_CLOCK":
        if not value.get("target_at"):
            raise ContractError(f"{where}.target_at is required for WALL_CLOCK")

    phase = value.get("session_phase")
    if phase is not None and phase not in SESSION_PHASES:
        raise ContractError(f"{where}.session_phase invalid: {phase!r}")


def _validate_temporal_contract(payload: dict[str, Any]) -> None:
    temporal = payload.get("temporal_contract")
    if temporal is None:
        return
    if not isinstance(temporal, dict):
        raise ContractError("temporal_contract must be an object")
    if temporal.get("version") != TEMPORAL_CONTRACT_VERSION:
        raise ContractError(
            f"temporal_contract.version must be {TEMPORAL_CONTRACT_VERSION!r}"
        )
    if temporal.get("time_basis") not in {"EXCHANGE_TRADING_TIME", "WALL_CLOCK"}:
        raise ContractError("temporal_contract.time_basis invalid")
    if not temporal.get("exchange_calendar"):
        raise ContractError("temporal_contract.exchange_calendar is required")

    heads = temporal.get("heads", [])
    if not isinstance(heads, list) or not heads:
        raise ContractError("temporal_contract.heads must be a non-empty array")
    seen_ids: set[str] = set()
    for index, head in enumerate(heads):
        if not isinstance(head, dict):
            raise ContractError(f"temporal_contract.heads[{index}] must be an object")
        head_id = str(head.get("head_id") or "")
        if not head_id or head_id in seen_ids:
            raise ContractError("temporal_contract head_id values must be unique and non-empty")
        seen_ids.add(head_id)
        if head.get("kind") not in HEAD_KINDS:
            raise ContractError(f"temporal_contract.heads[{index}].kind invalid")
        if not head.get("status"):
            raise ContractError(f"temporal_contract.heads[{index}].status is required")

    anchors = temporal.get("evaluation_anchors", [])
    if not isinstance(anchors, list):
        raise ContractError("temporal_contract.evaluation_anchors must be an array")
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, dict):
            raise ContractError(f"temporal_contract.evaluation_anchors[{index}] must be an object")
        if not anchor.get("name"):
            raise ContractError(f"temporal_contract.evaluation_anchors[{index}].name is required")
        _validate_time_coordinate(anchor.get("coordinate"), f"temporal_contract.evaluation_anchors[{index}].coordinate")


def _validate_trajectory(item: dict[str, Any], index: int) -> None:
    trajectory = item.get("trajectory")
    if trajectory is None:
        return
    if not isinstance(trajectory, dict):
        raise ContractError(f"forecasts[{index}].trajectory must be an object")
    source_kind = trajectory.get("source_kind")
    if source_kind not in TRAJECTORY_SOURCE_KINDS:
        raise ContractError(f"forecasts[{index}].trajectory.source_kind invalid: {source_kind!r}")
    points = trajectory.get("points")
    if not isinstance(points, list) or not points:
        raise ContractError(f"forecasts[{index}].trajectory.points must be a non-empty array")

    # V0.1 compatibility: session offsets remain valid. V0.2 also accepts an
    # explicit time_coordinate for multi-resolution paths. If both are present,
    # time_coordinate is authoritative and offset_sessions remains a UI hint.
    previous_offset: int | None = None
    for pidx, point in enumerate(points):
        if not isinstance(point, dict):
            raise ContractError(f"forecasts[{index}].trajectory.points[{pidx}] must be an object")
        if "time_coordinate" in point:
            _validate_time_coordinate(
                point.get("time_coordinate"),
                f"forecasts[{index}].trajectory.points[{pidx}].time_coordinate",
            )
        elif "offset_sessions" in point:
            try:
                offset = int(point.get("offset_sessions"))
            except (TypeError, ValueError) as exc:
                raise ContractError(
                    f"forecasts[{index}].trajectory.points[{pidx}].offset_sessions must be an integer"
                ) from exc
            if offset < 0 or (previous_offset is not None and offset <= previous_offset):
                raise ContractError(f"forecasts[{index}].trajectory offsets must be strictly increasing and >= 0")
            previous_offset = offset
        else:
            raise ContractError(
                f"forecasts[{index}].trajectory.points[{pidx}] requires time_coordinate or offset_sessions"
            )
        _numeric_quantiles(point.get("quantiles"), f"forecasts[{index}].trajectory.points[{pidx}]")

    confidence = item.get("confidence")
    if confidence is not None:
        if not isinstance(confidence, dict):
            raise ContractError(f"forecasts[{index}].confidence must be an object")
        cpoints = confidence.get("points")
        if not isinstance(cpoints, list) or not cpoints:
            raise ContractError(f"forecasts[{index}].confidence.points must be a non-empty array")
        prev: int | None = None
        for cidx, point in enumerate(cpoints):
            if not isinstance(point, dict):
                raise ContractError(f"forecasts[{index}].confidence.points[{cidx}] must be an object")
            try:
                score = float(point.get("score"))
            except (TypeError, ValueError) as exc:
                raise ContractError(f"forecasts[{index}].confidence.points[{cidx}].score must be numeric") from exc
            if not 0 <= score <= 100:
                raise ContractError(f"forecasts[{index}].confidence.points[{cidx}].score must be in [0,100]")
            if "time_coordinate" in point:
                _validate_time_coordinate(
                    point.get("time_coordinate"),
                    f"forecasts[{index}].confidence.points[{cidx}].time_coordinate",
                )
            elif "offset_sessions" in point:
                try:
                    offset = int(point.get("offset_sessions"))
                except (TypeError, ValueError) as exc:
                    raise ContractError(
                        f"forecasts[{index}].confidence.points[{cidx}].offset_sessions must be an integer"
                    ) from exc
                if offset < 0 or (prev is not None and offset <= prev):
                    raise ContractError(f"forecasts[{index}].confidence offsets must be strictly increasing and >= 0")
                prev = offset
            else:
                raise ContractError(
                    f"forecasts[{index}].confidence.points[{cidx}] requires time_coordinate or offset_sessions"
                )


def _validate_forecast(item: Any, index: int) -> None:
    if not isinstance(item, dict):
        raise ContractError(f"forecasts[{index}] must be an object")
    if not item.get("horizon"):
        raise ContractError(f"forecasts[{index}].horizon is required")
    if "target_coordinate" in item:
        _validate_time_coordinate(item.get("target_coordinate"), f"forecasts[{index}].target_coordinate")
    _validate_evidence(item.get("evidence_level"), f"forecasts[{index}]")
    _numeric_quantiles(item.get("quantiles"), f"forecasts[{index}]")
    if not item.get("model_version"):
        raise ContractError(f"forecasts[{index}].model_version is required")
    _validate_trajectory(item, index)


def _validate_history(payload: dict[str, Any]) -> None:
    history = payload.get("history")
    if history is None:
        return
    if not isinstance(history, dict):
        raise ContractError("history must be an object")
    if history.get("mode") not in HISTORY_MODES:
        raise ContractError(f"history.mode must be one of {sorted(HISTORY_MODES)}")
    points = history.get("points")
    if not isinstance(points, list) or not points:
        raise ContractError("history.points must be a non-empty array")
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise ContractError(f"history.points[{index}] must be an object")
        try:
            float(point.get("value"))
        except (TypeError, ValueError) as exc:
            raise ContractError(f"history.points[{index}].value must be numeric") from exc
        phase = point.get("session_phase")
        if phase is not None and phase not in SESSION_PHASES:
            raise ContractError(f"history.points[{index}].session_phase invalid: {phase!r}")


def validate_state(payload: dict[str, Any]) -> ValidatedState:
    if not isinstance(payload, dict):
        raise ContractError("InvestmentState must be an object")
    if payload.get("contract_version") != CONTRACT_VERSION:
        raise ContractError(f"contract_version must be {CONTRACT_VERSION!r}, got {payload.get('contract_version')!r}")
    if not payload.get("generated_at"):
        raise ContractError("generated_at is required")

    asset = _require_dict(payload, "asset")
    if not asset.get("ticker"):
        raise ContractError("asset.ticker is required")

    _validate_temporal_contract(payload)
    _validate_history(payload)

    forecasts = payload.get("forecasts", [])
    if not isinstance(forecasts, list):
        raise ContractError("forecasts must be an array")
    for index, forecast in enumerate(forecasts):
        _validate_forecast(forecast, index)

    events = payload.get("events", [])
    if not isinstance(events, list):
        raise ContractError("events must be an array")
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ContractError(f"events[{index}] must be an object")
        if not event.get("event_id") or not event.get("available_at"):
            raise ContractError(f"events[{index}] requires event_id and available_at")
        _validate_evidence(event.get("evidence_level"), f"events[{index}]")

    decision = _require_dict(payload, "decision")
    status = decision.get("status")
    if status not in V0_DECISION_STATUSES:
        raise ContractError(f"decision.status {status!r} is not permitted by Workbench V0; V0 cannot emit BUY/SELL candidates")
    _validate_evidence(decision.get("evidence_level"), "decision")

    provenance = _require_dict(payload, "provenance")
    if not provenance.get("publisher_version"):
        raise ContractError("provenance.publisher_version is required")

    return ValidatedState(payload=payload, sha256=state_sha256(payload))


def load_state(path: str | Path) -> ValidatedState:
    with Path(path).open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return validate_state(payload)
