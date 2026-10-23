"""Versioned, host-authored experiment inputs. No strategy rules or optimizer live here."""

import itertools
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator

from wallex_agent.contracts import Contract, Environment, UtcDatetime
from wallex_agent.contracts.hashing import content_id
from wallex_agent.runtime.config import RunConfig
from wallex_agent.strategies.c1_breakout import STRATEGY_ID
from wallex_agent.strategies.c2_trend_hold import STRATEGY_ID as C2_STRATEGY_ID
from wallex_agent.strategies.f2_tsm import STRATEGY_ID as F2_STRATEGY_ID

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
ParameterValue = StrictInt | StrictStr
# Bind the runtime's supported implementations to their config sections; the base config must make it active.
STRATEGY_SECTIONS = {STRATEGY_ID: "c1", C2_STRATEGY_ID: "c2", F2_STRATEGY_ID: "f2"}


def normalized(value: Any) -> Any:
    """Canonicalize equivalent decimal spellings without rounding or binary floats."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite financial value")
        text = format(value, "f")
        return (text.rstrip("0").rstrip(".") if "." in text else text) if value else "0"
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in cast(dict[str, Any], value).items()}
    if isinstance(value, (tuple, list)):
        return [normalized(v) for v in cast(tuple[Any, ...] | list[Any], value)]
    return value


def fingerprint(value: Contract | dict[str, Any]) -> str:
    return content_id(normalized(value.model_dump(mode="python") if isinstance(value, Contract) else value))


class TimeSplit(Contract):
    name: Identifier
    role: Literal["TRAIN", "VALIDATION", "TEST"]
    start_utc: UtcDatetime
    end_utc: UtcDatetime

    @model_validator(mode="after")
    def ordered(self) -> "TimeSplit":
        if self.start_utc >= self.end_utc:
            raise ValueError("split start must precede end (end is exclusive)")
        return self


class CostScenario(Contract):
    name: Identifier
    fee_rate: Decimal = Field(ge=0, lt=1, allow_inf_nan=False)
    slippage_bps: Decimal = Field(ge=0, lt=10000, allow_inf_nan=False)
    fee_source: Literal["ASSUMED", "ACCOUNT"] = "ASSUMED"

    @field_validator("fee_rate", "slippage_bps", mode="before")
    @classmethod
    def decimal_input(cls, value: Any) -> Any:
        if isinstance(value, (float, bool)):
            raise ValueError("use decimal strings for costs")
        return value


class ExperimentSpec(Contract):
    schema_version: Literal["1"]
    experiment_id: Identifier
    base_config: str
    dataset_dir: str
    dataset_resolution_seconds: Literal[60, 3600] = 60
    """Base bar resolution the dataset must declare (1h only for long research histories; exits then run per hour)."""
    strategy_id: str
    strategy_version: str = Field(min_length=1)
    seed: StrictInt
    max_trials: int = Field(gt=0)
    parameters: dict[str, tuple[ParameterValue, ...]] = {}
    splits: tuple[TimeSplit, ...] = Field(min_length=1)
    costs: tuple[CostScenario, ...] = Field(min_length=1)
    base_cost_id: Identifier
    min_trades_observation: int = Field(ge=0)

    @model_validator(mode="after")
    def chronological(self) -> "ExperimentSpec":
        if len({s.name for s in self.splits}) != len(self.splits):
            raise ValueError("duplicate split name")
        roles = {"TRAIN": 0, "VALIDATION": 1, "TEST": 2}
        for left, right in itertools.pairwise(self.splits):
            if left.end_utc > right.start_utc or roles[left.role] > roles[right.role]:
                raise ValueError("splits must be chronological, non-overlapping, TRAIN → VALIDATION → TEST")
        if not any(s.role != "TEST" for s in self.splits):
            raise ValueError("define a development period before TEST")
        if self.base_cost_id not in {c.name for c in self.costs}:
            raise ValueError("base_cost_id must name a cost scenario")
        if len({c.name for c in self.costs}) != len(self.costs):
            raise ValueError("duplicate cost name")
        costs = [fingerprint({"fee": c.fee_rate, "slippage": c.slippage_bps}) for c in self.costs]
        if len(set(costs)) != len(costs):
            raise ValueError("duplicate effective cost scenario")
        return self


@dataclass(frozen=True)
class ParameterConfig:
    parameter_hash: str
    parameters: dict[str, Any]
    config: RunConfig


def parameter_configs(spec: ExperimentSpec, base: RunConfig) -> tuple[ParameterConfig, ...]:
    if base.run.environment is not Environment.BACKTEST or base.agent.decision_provider != "BASELINE" or base.agent.use_wallex_mcp_tools:
        raise ValueError("experiments require BACKTEST with offline BASELINE")
    section = STRATEGY_SECTIONS.get(spec.strategy_id)
    if section is None or base.strategy is None or base.features is None or base.backtest is None:
        raise ValueError("strategy unsupported by current kernel, or incomplete backtest config")
    if base.strategy.active != spec.strategy_id:
        raise ValueError("base config's strategy.active must be the experiment's strategy_id")
    original = base.strategy.model_dump(mode="python")[section]
    if original is None:
        raise ValueError("base config lacks the strategy section")
    if original["version"] != spec.strategy_version:
        raise ValueError("strategy_version must match base config")
    keys = sorted(spec.parameters)
    if any(k not in original or k == "version" for k in keys):
        raise ValueError("unknown or forbidden strategy parameter")
    count = 1
    for key in keys:
        values = spec.parameters[key]
        if not values:
            raise ValueError("empty parameter axis")
        count *= len(values)
    if count > spec.max_trials:
        raise ValueError("parameter grid exceeds max_trials before expansion")
    variants: list[ParameterConfig] = []
    seen: set[str] = set()
    for values in itertools.product(*(spec.parameters[k] for k in keys)):
        raw = base.model_dump(mode="python")
        raw["strategy"][section].update(zip(keys, values))
        raw["run"]["seed"] = spec.seed
        cfg = RunConfig.model_validate(raw)
        parameters: dict[str, Any] = normalized(cfg.strategy.model_dump(mode="python")[section]) if cfg.strategy else {}
        identity: dict[str, Any] = {"strategy_id": spec.strategy_id, "parameters": parameters, "features": raw["features"],
                    "bar_interval_seconds": cfg.run.bar_interval_seconds}
        digest = fingerprint(identity)
        if digest in seen:
            raise ValueError("duplicate effective parameter combination")
        seen.add(digest)
        variants.append(ParameterConfig(digest, parameters, cfg))
    return tuple(variants)


def load_spec(path: Path) -> ExperimentSpec:
    return ExperimentSpec.model_validate(tomllib.loads(path.read_text()))
