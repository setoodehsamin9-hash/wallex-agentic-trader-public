from typing import Any

import pytest

from wallex_agent.experiments.spec import ExperimentSpec, parameter_configs
from wallex_agent.runtime.config import RunConfig


def test_grid_is_sorted_bounded_and_does_not_mutate_base(spec_data: dict[str, Any], base: RunConfig) -> None:
    spec_data["parameters"] = {"stop_atr": ["1.5", "2.5"], "lookback_bars": [20, 40]}
    before = base.model_dump_json()
    variants = parameter_configs(ExperimentSpec.model_validate(spec_data), base)
    assert [(p.config.strategy.c1.lookback_bars, str(p.config.strategy.c1.stop_atr)) for p in variants if p.config.strategy] == [
        (20, "1.5"), (20, "2.5"), (40, "1.5"), (40, "2.5")]
    assert base.model_dump_json() == before
    assert len({p.parameter_hash for p in variants}) == 4
    assert variants == parameter_configs(ExperimentSpec.model_validate(spec_data), base)


@pytest.mark.parametrize("change", ["overlap", "reverse", "naive", "unknown", "path", "future-train", "float", "cost-duplicate"])
def test_spec_rejects_unsafe_or_ambiguous_periods(spec_data: dict[str, Any], change: str) -> None:
    if change == "overlap":
        spec_data["splits"][1]["start_utc"] = "2026-10-01T23:00:00Z"
    elif change == "reverse":
        spec_data["splits"].reverse()
    elif change == "naive":
        spec_data["splits"][0]["start_utc"] = "2026-10-01T00:00:00"
    elif change == "unknown":
        spec_data["optimize_live"] = True
    elif change == "path":
        spec_data["experiment_id"] = "../escape"
    elif change == "future-train":
        spec_data["splits"][2]["role"] = "TRAIN"
    elif change == "float":
        spec_data["parameters"] = {"stop_atr": [1.5]}
    else:
        spec_data["costs"][1].update(fee_rate="0.00250", slippage_bps="5.0")
    with pytest.raises(ValueError):
        ExperimentSpec.model_validate(spec_data)


@pytest.mark.parametrize("parameters", [{"stop_atr": ["1.5", "1.50"]}, {"not_a_parameter": [1]}, {"lookback_bars": [0]},
                                         {"version": ["pretend"]}, {"lookback_bars": []}])
def test_invalid_or_duplicate_parameters_fail_before_execution(spec_data: dict[str, Any], base: RunConfig,
                                                               parameters: dict[str, list[Any]]) -> None:
    spec_data["parameters"] = parameters
    with pytest.raises(ValueError):
        parameter_configs(ExperimentSpec.model_validate(spec_data), base)


def test_parameter_hash_is_stable_across_mapping_order_and_decimal_spelling(spec_data: dict[str, Any], base: RunConfig) -> None:
    spec_data["parameters"] = {"lookback_bars": [16], "stop_atr": ["1.50"]}
    a = parameter_configs(ExperimentSpec.model_validate(spec_data), base)[0]
    spec_data["parameters"] = {"stop_atr": ["1.5"], "lookback_bars": [16]}
    b = parameter_configs(ExperimentSpec.model_validate(spec_data), base)[0]
    assert a.parameter_hash == b.parameter_hash
