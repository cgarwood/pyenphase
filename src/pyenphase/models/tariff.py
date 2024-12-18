"""Model for the Envoy tariff data."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EnvoyStorageMode(StrEnum):
    BACKUP = "backup"
    SELF_CONSUMPTION = "self-consumption"
    SAVINGS = "economy"
    LEGACY_SAVINGS = "savings-mode"


@dataclass
class EnvoyTariff:
    """Model for the Envoy tariff data."""

    currency: dict[str, Any]
    logger: str | None
    date: str | None
    storage_settings: EnvoyStorageSettings | None
    single_rate: dict[str, Any]
    seasons: list[Any]
    seasons_sell: list[Any] | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> EnvoyTariff:
        """Initialize from the API."""
        return cls(
            currency=data["currency"],
            logger=data.get("logger"),
            date=data.get("date"),
            storage_settings=(
                EnvoyStorageSettings.from_api(data["storage_settings"])
                if data.get("storage_settings")
                else None
            ),
            single_rate=data["single_rate"],
            seasons=data["seasons"],
            seasons_sell=data.get("seasons_sell"),
        )

    def to_api(self) -> dict[str, Any]:
        """Convert to API format."""
        retval = {
            "currency": self.currency,
            "single_rate": self.single_rate,
            "seasons": self.seasons,
        }
        if self.logger:
            retval["logger"] = self.logger
        if self.date:
            retval["date"] = self.date
        if self.seasons_sell:
            retval["seasons_sell"] = self.seasons_sell
        if self.storage_settings:
            retval["storage_settings"] = self.storage_settings.to_api()

        return retval


@dataclass
class EnvoyStorageSettings:
    """Model for the Envoy storage settings."""

    mode: EnvoyStorageMode
    operation_mode_sub_type: str
    reserved_soc: float
    very_low_soc: int
    charge_from_grid: bool
    date: str | None
    opt_schedules: bool | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> EnvoyStorageSettings:
        """Initialize from the API."""
        return cls(
            # It appears a `mode` value of `economy` and `savings-mode` is interchangeable
            # However, the Enlighten app is using the `economy` value, so we will convert
            # `savings-mode` to `economy`
            mode=(
                EnvoyStorageMode.SAVINGS
                if data["mode"] == EnvoyStorageMode.LEGACY_SAVINGS.value
                else EnvoyStorageMode(data["mode"])
            ),
            operation_mode_sub_type=data["operation_mode_sub_type"],
            reserved_soc=data["reserved_soc"],
            very_low_soc=data["very_low_soc"],
            charge_from_grid=data["charge_from_grid"],
            # Some firmware versions don't return date
            date=data.get("date"),
            # opt_schedules was added in 8.2.42xx
            opt_schedules=data.get("opt_schedules"),
        )

    def to_api(self) -> dict[str, Any]:
        """Convert to API format."""
        retval = {
            "mode": self.mode.value,
            "operation_mode_sub_type": self.operation_mode_sub_type,
            "reserved_soc": self.reserved_soc,
            "very_low_soc": self.very_low_soc,
            "charge_from_grid": self.charge_from_grid,
        }
        if self.date is not None:
            retval["date"] = self.date
        # opt_schedules was added in 8.2.42xx
        if self.opt_schedules is not None:
            retval["opt_schedules"] = self.opt_schedules

        return retval
