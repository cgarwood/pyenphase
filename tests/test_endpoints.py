"""Test endpoint for envoy v7 and newer firmware"""

import json
import logging
from dataclasses import replace
from os import listdir
from os.path import isfile, join
from typing import Any

import httpx
import orjson
import pytest
import respx
from httpx import Response
from syrupy.assertion import SnapshotAssertion

from pyenphase.const import (
    URL_DRY_CONTACT_SETTINGS,
    URL_DRY_CONTACT_STATUS,
    URL_GRID_RELAY,
    URL_TARIFF,
    PhaseNames,
)
from pyenphase.envoy import EnvoyProbeFailed, SupportedFeatures
from pyenphase.exceptions import EnvoyError, EnvoyFeatureNotAvailable
from pyenphase.models.dry_contacts import DryContactStatus
from pyenphase.models.meters import CtMeterStatus, CtType, EnvoyPhaseMode
from pyenphase.models.tariff import EnvoyStorageMode

from .common import (
    get_mock_envoy,
    load_fixture,
    load_json_fixture,
    start_7_firmware_mock,
    updater_features,
)

LOGGER = logging.getLogger(__name__)


@pytest.mark.parametrize(
    (
        "version",
        "part_number",
        "supported_features",
        "updaters",
        "phase_count",
        "common_properties",
        "production_phases",
        "consumption_phases",
        "ct_production",
        "ct_consumption",
        "ct_storage",
        "ct_production_phases",
        "ct_consumption_phases",
        "ct_storage_phases",
    ),
    [
        (
            "5.0.62",
            "800-00551-r02",
            SupportedFeatures.INVERTERS
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.TARIFF,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyApiV1ProductionUpdater": SupportedFeatures.PRODUCTION,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
            },
            1,
            {
                "ctMeters": 0,
                "phaseCount": 1,
                "phaseMode": None,
                "consumptionMeter": None,
                "productionMeter": None,
                "storageMeter": None,
            },
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
        ),
        (
            "4.10.35",
            "800-00555-r03",
            SupportedFeatures.METERING
            | SupportedFeatures.INVERTERS
            | SupportedFeatures.TOTAL_CONSUMPTION
            | SupportedFeatures.NET_CONSUMPTION
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.TARIFF
            | SupportedFeatures.DUALPHASE
            | SupportedFeatures.CTMETERS,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyProductionJsonUpdater": SupportedFeatures.METERING
                | SupportedFeatures.TOTAL_CONSUMPTION
                | SupportedFeatures.NET_CONSUMPTION
                | SupportedFeatures.PRODUCTION,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
                "EnvoyMetersUpdater": SupportedFeatures.DUALPHASE
                | SupportedFeatures.CTMETERS,
            },
            2,
            {
                "ctMeters": 2,
                "phaseCount": 2,
                "phaseMode": EnvoyPhaseMode.SPLIT,
                "consumptionMeter": CtType.NET_CONSUMPTION,
                "productionMeter": CtType.PRODUCTION,
                "storageMeter": None,
            },
            {},
            {},
            {
                "eid": 704643328,
                "active_power": 166,
                "measurement_type": CtType.PRODUCTION,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {
                "eid": 704643584,
                "active_power": 567,
                "measurement_type": CtType.NET_CONSUMPTION,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {},
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385169,
                    "active_power": 83,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385170,
                    "active_power": 84,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385425,
                    "active_power": 394,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385426,
                    "active_power": 173,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
            {},
        ),
        (
            "7.3.130",
            "800-00555-r03",
            SupportedFeatures.METERING
            | SupportedFeatures.TOTAL_CONSUMPTION
            | SupportedFeatures.NET_CONSUMPTION
            | SupportedFeatures.INVERTERS
            | SupportedFeatures.PRODUCTION,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyProductionJsonUpdater": SupportedFeatures.METERING
                | SupportedFeatures.TOTAL_CONSUMPTION
                | SupportedFeatures.NET_CONSUMPTION
                | SupportedFeatures.PRODUCTION,
            },
            1,
            {
                "ctMeters": 0,
                "phaseCount": 1,
                "phaseMode": None,
                "consumptionMeter": None,
                "productionMeter": None,
                "storageMeter": None,
            },
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
        ),
        (
            "7.3.130_no_consumption",
            "800-00647-r10",
            SupportedFeatures.METERING
            | SupportedFeatures.INVERTERS
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.TARIFF
            | SupportedFeatures.DUALPHASE
            | SupportedFeatures.CTMETERS,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyProductionJsonUpdater": SupportedFeatures.METERING
                | SupportedFeatures.PRODUCTION,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
                "EnvoyMetersUpdater": SupportedFeatures.DUALPHASE
                | SupportedFeatures.CTMETERS,
            },
            2,
            {
                "ctMeters": 1,
                "phaseCount": 2,
                "phaseMode": EnvoyPhaseMode.SPLIT,
                "consumptionMeter": None,
                "productionMeter": CtType.PRODUCTION,
                "storageMeter": None,
            },
            {},
            {},
            {
                "eid": 704643328,
                "active_power": 3625,
                "measurement_type": CtType.PRODUCTION,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {},
            {},
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385169,
                    "active_power": 1811,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385170,
                    "active_power": 1814,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
            {},
            {},
        ),
        (
            "7.3.517",
            "800-00555-r03",
            SupportedFeatures.METERING
            | SupportedFeatures.TOTAL_CONSUMPTION
            | SupportedFeatures.NET_CONSUMPTION
            | SupportedFeatures.ENPOWER
            | SupportedFeatures.ENCHARGE
            | SupportedFeatures.INVERTERS
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.TARIFF,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyProductionJsonUpdater": SupportedFeatures.METERING
                | SupportedFeatures.TOTAL_CONSUMPTION
                | SupportedFeatures.NET_CONSUMPTION
                | SupportedFeatures.PRODUCTION,
                "EnvoyEnembleUpdater": SupportedFeatures.ENPOWER
                | SupportedFeatures.ENCHARGE,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
            },
            1,
            {
                "ctMeters": 0,
                "phaseCount": 1,
                "phaseMode": None,
                "consumptionMeter": None,
                "productionMeter": None,
                "storageMeter": None,
            },
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
        ),
        (
            "7.3.517_legacy_savings_mode",
            "800-00555-r03",
            SupportedFeatures.METERING
            | SupportedFeatures.TOTAL_CONSUMPTION
            | SupportedFeatures.NET_CONSUMPTION
            | SupportedFeatures.ENPOWER
            | SupportedFeatures.ENCHARGE
            | SupportedFeatures.INVERTERS
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.TARIFF,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyProductionJsonUpdater": SupportedFeatures.METERING
                | SupportedFeatures.TOTAL_CONSUMPTION
                | SupportedFeatures.NET_CONSUMPTION
                | SupportedFeatures.PRODUCTION,
                "EnvoyEnembleUpdater": SupportedFeatures.ENPOWER
                | SupportedFeatures.ENCHARGE,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
            },
            1,
            {
                "ctMeters": 0,
                "phaseCount": 1,
                "phaseMode": None,
                "consumptionMeter": None,
                "productionMeter": None,
                "storageMeter": None,
            },
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
        ),
        (
            "7.3.517_system_2",
            "800-00555-r03",
            SupportedFeatures.METERING
            | SupportedFeatures.TOTAL_CONSUMPTION
            | SupportedFeatures.NET_CONSUMPTION
            | SupportedFeatures.ENPOWER
            | SupportedFeatures.ENCHARGE
            | SupportedFeatures.INVERTERS
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.TARIFF
            | SupportedFeatures.DUALPHASE
            | SupportedFeatures.CTMETERS,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyProductionJsonUpdater": SupportedFeatures.METERING
                | SupportedFeatures.TOTAL_CONSUMPTION
                | SupportedFeatures.NET_CONSUMPTION
                | SupportedFeatures.PRODUCTION,
                "EnvoyEnembleUpdater": SupportedFeatures.ENPOWER
                | SupportedFeatures.ENCHARGE,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
                "EnvoyMetersUpdater": SupportedFeatures.DUALPHASE
                | SupportedFeatures.CTMETERS,
            },
            2,
            {
                "ctMeters": 2,
                "phaseCount": 2,
                "phaseMode": EnvoyPhaseMode.SPLIT,
                "consumptionMeter": CtType.NET_CONSUMPTION,
                "productionMeter": CtType.PRODUCTION,
                "storageMeter": None,
            },
            {},
            {},
            {
                "eid": 704643328,
                "active_power": 2660,
                "measurement_type": CtType.PRODUCTION,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {
                "eid": 704643584,
                "active_power": 23,
                "measurement_type": CtType.NET_CONSUMPTION,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {},
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385169,
                    "active_power": 1331,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385170,
                    "active_power": 1329,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385425,
                    "active_power": -17,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385426,
                    "active_power": 41,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
            {},
        ),
        (
            "7.3.466_metered_disabled_cts",
            "800-00654-r08",
            SupportedFeatures.INVERTERS
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.TARIFF,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyProductionJsonFallbackUpdater": SupportedFeatures.PRODUCTION,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
            },
            1,
            {
                "ctMeters": 0,
                "phaseCount": 1,
                "phaseMode": None,
                "consumptionMeter": None,
                "productionMeter": None,
                "storageMeter": None,
            },
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
        ),
        (
            "7.6.114_without_cts",
            "800-00656-r06",
            SupportedFeatures.INVERTERS | SupportedFeatures.PRODUCTION,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyApiV1ProductionUpdater": SupportedFeatures.PRODUCTION,
            },
            1,
            {
                "ctMeters": 0,
                "phaseCount": 1,
                "phaseMode": None,
                "consumptionMeter": None,
                "productionMeter": None,
                "storageMeter": None,
            },
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
        ),
        (
            "7.6.175",
            "800-00555-r03",
            SupportedFeatures.INVERTERS | SupportedFeatures.PRODUCTION,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyApiV1ProductionUpdater": SupportedFeatures.PRODUCTION,
            },
            1,
            {
                "ctMeters": 0,
                "phaseCount": 1,
                "phaseMode": None,
                "consumptionMeter": None,
                "productionMeter": None,
                "storageMeter": None,
            },
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
        ),
        (
            "7.6.175_total",
            "800-00654-r06",
            SupportedFeatures.INVERTERS
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.TARIFF,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyProductionJsonFallbackUpdater": SupportedFeatures.PRODUCTION,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
            },
            1,
            {
                "ctMeters": 0,
                "phaseCount": 1,
                "phaseMode": None,
                "consumptionMeter": None,
                "productionMeter": None,
                "storageMeter": None,
            },
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
        ),
        (
            "7.6.175_standard",
            "800-00656-r06",
            SupportedFeatures.INVERTERS | SupportedFeatures.PRODUCTION,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyApiV1ProductionUpdater": SupportedFeatures.PRODUCTION,
            },
            1,
            {
                "ctMeters": 0,
                "phaseCount": 1,
                "phaseMode": None,
                "consumptionMeter": None,
                "productionMeter": None,
                "storageMeter": None,
            },
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
        ),
        (
            "7.6.175_with_cts",
            "800-00654-r08",
            SupportedFeatures.INVERTERS
            | SupportedFeatures.METERING
            | SupportedFeatures.TOTAL_CONSUMPTION
            | SupportedFeatures.NET_CONSUMPTION
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.TARIFF
            | SupportedFeatures.CTMETERS,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyProductionJsonUpdater": SupportedFeatures.METERING
                | SupportedFeatures.TOTAL_CONSUMPTION
                | SupportedFeatures.NET_CONSUMPTION
                | SupportedFeatures.PRODUCTION,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
                "EnvoyMetersUpdater": SupportedFeatures.CTMETERS,
            },
            1,
            {
                "ctMeters": 2,
                "phaseCount": 1,
                "phaseMode": EnvoyPhaseMode.THREE,
                "consumptionMeter": CtType.NET_CONSUMPTION,
                "productionMeter": CtType.PRODUCTION,
                "storageMeter": None,
            },
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
        ),
        (
            "7.6.175_with_cts_3phase",
            "800-00654-r08",
            SupportedFeatures.INVERTERS
            | SupportedFeatures.METERING
            | SupportedFeatures.TOTAL_CONSUMPTION
            | SupportedFeatures.NET_CONSUMPTION
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.TARIFF
            | SupportedFeatures.THREEPHASE
            | SupportedFeatures.CTMETERS,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyProductionJsonUpdater": SupportedFeatures.METERING
                | SupportedFeatures.TOTAL_CONSUMPTION
                | SupportedFeatures.NET_CONSUMPTION
                | SupportedFeatures.PRODUCTION,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
                "EnvoyMetersUpdater": SupportedFeatures.THREEPHASE
                | SupportedFeatures.CTMETERS,
            },
            3,
            {
                "ctMeters": 2,
                "phaseCount": 3,
                "phaseMode": EnvoyPhaseMode.THREE,
                "consumptionMeter": CtType.NET_CONSUMPTION,
                "productionMeter": CtType.PRODUCTION,
                "storageMeter": None,
            },
            {
                PhaseNames.PHASE_1: {
                    "watt_hours_lifetime": 1869678,
                    "watt_hours_last_7_days": 29891,
                    "watt_hours_today": 2200,
                    "watts_now": -3,
                },
                PhaseNames.PHASE_2: {
                    "watt_hours_lifetime": 1241246,
                    "watt_hours_last_7_days": 19794,
                    "watt_hours_today": 1455,
                    "watts_now": 0,
                },
                PhaseNames.PHASE_3: {
                    "watt_hours_lifetime": 1240189,
                    "watt_hours_last_7_days": 19807,
                    "watt_hours_today": 1458,
                    "watts_now": -4,
                },
            },
            {
                PhaseNames.PHASE_1: {
                    "watt_hours_lifetime": 2293783,
                    "watt_hours_last_7_days": 39392,
                    "watt_hours_today": 8585,
                    "watts_now": 89,
                },
                PhaseNames.PHASE_2: {
                    "watt_hours_lifetime": 948058,
                    "watt_hours_last_7_days": 18949,
                    "watt_hours_today": 2155,
                    "watts_now": 123,
                },
                PhaseNames.PHASE_3: {
                    "watt_hours_lifetime": 832954,
                    "watt_hours_last_7_days": 10443,
                    "watt_hours_today": 1683,
                    "watts_now": -3,
                },
            },
            {
                "eid": 704643328,
                "active_power": 489,
                "measurement_type": CtType.PRODUCTION,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {
                "eid": 704643584,
                "active_power": -36,
                "measurement_type": CtType.NET_CONSUMPTION,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {},
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385169,
                    "active_power": 489,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385170,
                    "active_power": 0,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_3: {
                    "eid": 1778385171,
                    "active_power": -1,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385425,
                    "active_power": -36,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385426,
                    "active_power": -0,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_3: {
                    "eid": 1778385427,
                    "active_power": -0,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
            {},
        ),
        (
            "7.3.466_with_cts_3phase",
            "800-00654-r08",
            SupportedFeatures.INVERTERS
            | SupportedFeatures.METERING
            | SupportedFeatures.TOTAL_CONSUMPTION
            | SupportedFeatures.NET_CONSUMPTION
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.TARIFF
            | SupportedFeatures.THREEPHASE
            | SupportedFeatures.CTMETERS,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyProductionJsonUpdater": SupportedFeatures.METERING
                | SupportedFeatures.TOTAL_CONSUMPTION
                | SupportedFeatures.NET_CONSUMPTION
                | SupportedFeatures.PRODUCTION,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
                "EnvoyMetersUpdater": SupportedFeatures.THREEPHASE
                | SupportedFeatures.CTMETERS,
            },
            3,
            {
                "ctMeters": 2,
                "phaseCount": 3,
                "phaseMode": EnvoyPhaseMode.THREE,
                "consumptionMeter": CtType.NET_CONSUMPTION,
                "productionMeter": CtType.PRODUCTION,
                "storageMeter": None,
            },
            {
                PhaseNames.PHASE_1: {
                    "watt_hours_lifetime": 1869678,
                    "watt_hours_last_7_days": 29891,
                    "watt_hours_today": 2200,
                    "watts_now": -3,
                },
                PhaseNames.PHASE_2: {
                    "watt_hours_lifetime": 1241246,
                    "watt_hours_last_7_days": 19794,
                    "watt_hours_today": 1455,
                    "watts_now": 0,
                },
                PhaseNames.PHASE_3: {
                    "watt_hours_lifetime": 1240189,
                    "watt_hours_last_7_days": 19807,
                    "watt_hours_today": 1458,
                    "watts_now": -4,
                },
            },
            {
                PhaseNames.PHASE_1: {
                    "watt_hours_lifetime": 2293783,
                    "watt_hours_last_7_days": 39392,
                    "watt_hours_today": 8585,
                    "watts_now": 89,
                },
                PhaseNames.PHASE_2: {
                    "watt_hours_lifetime": 948058,
                    "watt_hours_last_7_days": 18949,
                    "watt_hours_today": 2155,
                    "watts_now": 123,
                },
                PhaseNames.PHASE_3: {
                    "watt_hours_lifetime": 832954,
                    "watt_hours_last_7_days": 10443,
                    "watt_hours_today": 1683,
                    "watts_now": -3,
                },
            },
            {
                "eid": 704643328,
                "active_power": 489,
                "measurement_type": CtType.PRODUCTION,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {
                "eid": 704643584,
                "active_power": -36,
                "measurement_type": CtType.NET_CONSUMPTION,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {},
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385169,
                    "active_power": 489,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385170,
                    "active_power": 0,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_3: {
                    "eid": 1778385171,
                    "active_power": -1,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385425,
                    "active_power": -36,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385426,
                    "active_power": -0,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_3: {
                    "eid": 1778385427,
                    "active_power": -0,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
            {},
        ),
        (
            "7.6.185_with_cts_and_battery_3t",
            "800-00654-r08",
            SupportedFeatures.INVERTERS
            | SupportedFeatures.METERING
            | SupportedFeatures.TOTAL_CONSUMPTION
            | SupportedFeatures.NET_CONSUMPTION
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.ENCHARGE
            | SupportedFeatures.TARIFF
            | SupportedFeatures.CTMETERS,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyEnembleUpdater": SupportedFeatures.ENCHARGE,
                "EnvoyProductionJsonUpdater": SupportedFeatures.METERING
                | SupportedFeatures.TOTAL_CONSUMPTION
                | SupportedFeatures.NET_CONSUMPTION
                | SupportedFeatures.PRODUCTION,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
                "EnvoyMetersUpdater": SupportedFeatures.CTMETERS,
            },
            1,
            {
                "ctMeters": 2,
                "phaseCount": 1,
                "phaseMode": EnvoyPhaseMode.THREE,
                "consumptionMeter": CtType.NET_CONSUMPTION,
                "productionMeter": CtType.PRODUCTION,
                "storageMeter": None,
            },
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
        ),
        (
            "8.1.41",
            "800-00664-r05",
            SupportedFeatures.INVERTERS
            | SupportedFeatures.METERING
            | SupportedFeatures.TOTAL_CONSUMPTION
            | SupportedFeatures.NET_CONSUMPTION
            | SupportedFeatures.ENCHARGE
            | SupportedFeatures.ENPOWER
            | SupportedFeatures.PRODUCTION,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyEnembleUpdater": SupportedFeatures.ENPOWER
                | SupportedFeatures.ENCHARGE,
                "EnvoyProductionJsonUpdater": SupportedFeatures.PRODUCTION
                | SupportedFeatures.METERING
                | SupportedFeatures.TOTAL_CONSUMPTION
                | SupportedFeatures.NET_CONSUMPTION,
            },
            1,
            {
                "ctMeters": 0,
                "phaseCount": 1,
                "phaseMode": None,
                "consumptionMeter": None,
                "productionMeter": None,
                "storageMeter": None,
            },
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
        ),
        (
            "8.2.127_with_3cts_and_battery_split",
            "800-00654-r08",
            SupportedFeatures.INVERTERS
            | SupportedFeatures.METERING
            | SupportedFeatures.TOTAL_CONSUMPTION
            | SupportedFeatures.NET_CONSUMPTION
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.ENCHARGE
            | SupportedFeatures.ENPOWER
            | SupportedFeatures.TARIFF
            | SupportedFeatures.DUALPHASE
            | SupportedFeatures.CTMETERS,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyEnembleUpdater": SupportedFeatures.ENCHARGE
                | SupportedFeatures.ENPOWER,
                "EnvoyProductionJsonUpdater": SupportedFeatures.METERING
                | SupportedFeatures.TOTAL_CONSUMPTION
                | SupportedFeatures.NET_CONSUMPTION
                | SupportedFeatures.PRODUCTION,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
                "EnvoyMetersUpdater": SupportedFeatures.DUALPHASE
                | SupportedFeatures.CTMETERS,
            },
            2,
            {
                "ctMeters": 3,
                "phaseCount": 2,
                "phaseMode": EnvoyPhaseMode.SPLIT,
                "consumptionMeter": CtType.NET_CONSUMPTION,
                "productionMeter": CtType.PRODUCTION,
                "storageMeter": CtType.STORAGE,
            },
            {},
            {},
            {
                "eid": 704643328,
                "active_power": 1714,
                "measurement_type": CtType.PRODUCTION,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {
                "eid": 704643584,
                "active_power": 129,
                "measurement_type": CtType.NET_CONSUMPTION,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {
                "eid": 704643840,
                "active_power": -2580,
                "measurement_type": CtType.STORAGE,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385169,
                    "active_power": 856,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385170,
                    "active_power": 858,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385425,
                    "active_power": -201,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385426,
                    "active_power": 331,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385681,
                    "active_power": -2115,
                    "measurement_type": CtType.STORAGE,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385682,
                    "active_power": -465,
                    "measurement_type": CtType.STORAGE,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
        ),
        (
            "8.2.127_with_generator_running",
            "800-00647-r09",
            SupportedFeatures.INVERTERS
            | SupportedFeatures.METERING
            | SupportedFeatures.TOTAL_CONSUMPTION
            | SupportedFeatures.NET_CONSUMPTION
            | SupportedFeatures.PRODUCTION
            | SupportedFeatures.ENCHARGE
            | SupportedFeatures.ENPOWER
            | SupportedFeatures.TARIFF
            | SupportedFeatures.DUALPHASE
            | SupportedFeatures.CTMETERS
            | SupportedFeatures.GENERATOR,
            {
                "EnvoyApiV1ProductionInvertersUpdater": SupportedFeatures.INVERTERS,
                "EnvoyEnembleUpdater": SupportedFeatures.ENCHARGE
                | SupportedFeatures.ENPOWER,
                "EnvoyProductionJsonUpdater": SupportedFeatures.METERING
                | SupportedFeatures.TOTAL_CONSUMPTION
                | SupportedFeatures.NET_CONSUMPTION
                | SupportedFeatures.PRODUCTION,
                "EnvoyTariffUpdater": SupportedFeatures.TARIFF,
                "EnvoyMetersUpdater": SupportedFeatures.DUALPHASE
                | SupportedFeatures.CTMETERS,
                "EnvoyGeneratorUpdater": SupportedFeatures.GENERATOR,
            },
            2,
            {
                "ctMeters": 2,
                "phaseCount": 2,
                "phaseMode": EnvoyPhaseMode.SPLIT,
                "consumptionMeter": CtType.NET_CONSUMPTION,
                "productionMeter": CtType.PRODUCTION,
                "storageMeter": None,
            },
            {},
            {},
            {
                "eid": 704643328,
                "active_power": 2336,
                "measurement_type": CtType.PRODUCTION,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {
                "eid": 704643584,
                "active_power": 196,
                "measurement_type": CtType.NET_CONSUMPTION,
                "metering_status": CtMeterStatus.NORMAL,
            },
            {},
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385169,
                    "active_power": 1173,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385170,
                    "active_power": 1163,
                    "measurement_type": CtType.PRODUCTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
            {
                PhaseNames.PHASE_1: {
                    "eid": 1778385425,
                    "active_power": 268,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
                PhaseNames.PHASE_2: {
                    "eid": 1778385426,
                    "active_power": -72,
                    "measurement_type": CtType.NET_CONSUMPTION,
                    "metering_status": CtMeterStatus.NORMAL,
                },
            },
            {},
        ),
    ],
    ids=[
        "5.0.62",
        "4.10.35",
        "7.3.130",
        "7.3.130_no_consumption",
        "7.3.517",
        "7.3.517_legacy_savings_mode",
        "7.3.517_system_2",
        "7.3.466_metered_disabled_cts",
        "7.6.114_without_cts",
        "7.6.175",
        "7.6.175_total",
        "7.6.175_standard",
        "7.6.175_with_cts",
        "7.6.175_with_cts_3phase",
        "7.3.466_with_cts_3phase",
        "7.6.185_with_cts_and_battery_3t",
        "8.1.41",
        "8.2.127_with_3cts_and_battery_split",
        "8.2.127_with_generator_running",
    ],
)
@pytest.mark.asyncio
@respx.mock
async def test_with_7_x_firmware(
    version: str,
    part_number: str,
    snapshot: SnapshotAssertion,
    supported_features: SupportedFeatures,
    updaters: dict[str, SupportedFeatures],
    caplog: pytest.LogCaptureFixture,
    phase_count: int,
    common_properties: dict[str, Any],
    production_phases: dict[str, dict[str, Any]],
    consumption_phases: dict[str, dict[str, Any]],
    ct_production: dict[str, Any],
    ct_consumption: dict[str, Any],
    ct_storage: dict[str, Any],
    ct_production_phases: dict[str, dict[str, Any]],
    ct_consumption_phases: dict[str, dict[str, Any]],
    ct_storage_phases: dict[str, dict[str, Any]],
) -> None:
    """Verify with 7.x firmware."""
    logging.getLogger("pyenphase").setLevel(logging.DEBUG)
    start_7_firmware_mock()
    path = f"tests/fixtures/{version}"
    files = [f for f in listdir(path) if isfile(join(path, f))]
    respx.get("/info").mock(
        return_value=Response(200, text=load_fixture(version, "info"))
    )
    respx.get("/info.xml").mock(return_value=Response(200, text=""))
    if "production" in files:
        try:
            json_data = load_json_fixture(version, "production")
        except json.decoder.JSONDecodeError:
            json_data = None
        respx.get("/production").mock(return_value=Response(200, json=json_data))
    else:
        respx.get("/production").mock(return_value=Response(404))
    if "production.json" in files:
        respx.get("/production.json").mock(
            return_value=Response(
                200, json=load_json_fixture(version, "production.json")
            )
        )
        respx.get("/production.json?details=1").mock(
            return_value=Response(
                200, json=load_json_fixture(version, "production.json")
            )
        )
    else:
        respx.get("/production.json").mock(return_value=Response(404))
        respx.get("/production.json?details=1").mock(return_value=Response(404))
    respx.get("/api/v1/production").mock(
        return_value=Response(200, json=load_json_fixture(version, "api_v1_production"))
    )
    respx.get("/api/v1/production/inverters").mock(
        return_value=Response(
            200, json=load_json_fixture(version, "api_v1_production_inverters")
        )
    )
    respx.get("/ivp/ensemble/inventory").mock(
        return_value=Response(
            200, json=load_json_fixture(version, "ivp_ensemble_inventory")
        )
    )
    if "ivp_ensemble_dry_contacts" in files:
        try:
            json_data = load_json_fixture(version, "ivp_ensemble_dry_contacts")
        except json.decoder.JSONDecodeError:
            json_data = None
        respx.get("/ivp/ensemble/dry_contacts").mock(
            return_value=Response(200, json=json_data)
        )
        respx.post("/ivp/ensemble/dry_contacts").mock(
            return_value=Response(200, json=json_data)
        )
    if "ivp_ss_dry_contact_settings" in files:
        try:
            json_data = load_json_fixture(version, "ivp_ss_dry_contact_settings")
        except json.decoder.JSONDecodeError:
            json_data = None
        respx.get("/ivp/ss/dry_contact_settings").mock(
            return_value=Response(200, json=json_data)
        )
        respx.post("/ivp/ss/dry_contact_settings").mock(
            return_value=Response(200, json=json_data)
        )
    if "ivp_ensemble_power" in files:
        try:
            json_data = load_json_fixture(version, "ivp_ensemble_power")
        except json.decoder.JSONDecodeError:
            json_data = None
        respx.get("/ivp/ensemble/power").mock(
            return_value=Response(200, json=json_data)
        )

    if "ivp_ensemble_secctrl" in files:
        try:
            json_data = load_json_fixture(version, "ivp_ensemble_secctrl")
        except json.decoder.JSONDecodeError:
            json_data = None
        respx.get("/ivp/ensemble/secctrl").mock(
            return_value=Response(200, json=json_data)
        )

    if "admin_lib_tariff" in files:
        try:
            json_data = load_json_fixture(version, "admin_lib_tariff")
        except json.decoder.JSONDecodeError:
            json_data = None
        respx.get("/admin/lib/tariff").mock(return_value=Response(200, json=json_data))
        respx.put("/admin/lib/tariff").mock(return_value=Response(200, json=json_data))
    else:
        respx.get("/admin/lib/tariff").mock(return_value=Response(404))

    if "ivp_meters" in files:
        respx.get("/ivp/meters").mock(
            return_value=Response(200, json=load_json_fixture(version, "ivp_meters"))
        )
    else:
        respx.get("/ivp/meters").mock(return_value=Response(404))

    if "ivp_meters_readings" in files:
        respx.get("/ivp/meters/readings").mock(
            return_value=Response(
                200, json=load_json_fixture(version, "ivp_meters_readings")
            )
        )
    else:
        respx.get("/ivp/meters/readings").mock(return_value=Response(404))

    if "ivp_ss_gen_config" in files:
        try:
            json_data = load_json_fixture(version, "ivp_ss_gen_config")
        except json.decoder.JSONDecodeError:
            json_data = {}
        respx.get("/ivp/ss/gen_config").mock(return_value=Response(200, json=json_data))
    else:
        respx.get("/ivp/ss/gen_config").mock(return_value=Response(200, json={}))

    caplog.set_level(logging.DEBUG)

    envoy = await get_mock_envoy()
    data = envoy.data
    assert data == snapshot

    assert envoy.firmware == version.split("_")[0]
    assert envoy.serial_number

    assert envoy.part_number == part_number
    assert updater_features(envoy._updaters) == updaters
    # We're testing, disable warning on private member
    # pylint: disable=protected-access
    assert envoy._supported_features == supported_features

    if supported_features & supported_features.ENPOWER:
        # switch off debug for one post to improve COV
        logging.getLogger("pyenphase").setLevel(logging.WARN)
        respx.post(URL_GRID_RELAY).mock(return_value=Response(200, json={}))
        await envoy.go_on_grid()
        logging.getLogger("pyenphase").setLevel(logging.DEBUG)
        assert respx.calls.last.request.content == orjson.dumps(
            {"mains_admin_state": "closed"}
        )
        await envoy.go_off_grid()
        assert respx.calls.last.request.content == orjson.dumps(
            {"mains_admin_state": "open"}
        )

        # Test updating dry contacts
        with pytest.raises(ValueError):
            await envoy.update_dry_contact({"missing": "id"})

        with pytest.raises(ValueError):
            bad_envoy = await get_mock_envoy(False)
            await bad_envoy.probe()
            await bad_envoy.update_dry_contact({"id": "NC1"})

        dry_contact = envoy.data.dry_contact_settings["NC1"]
        new_data: dict[str, Any] = {"id": "NC1", "load_name": "NC1 Test"}
        new_model = replace(dry_contact, **new_data)

        await envoy.update_dry_contact(new_data)
        assert respx.calls.last.request.content == orjson.dumps(
            {"dry_contacts": new_model.to_api()}
        )

        if envoy.data.dry_contact_settings["NC1"].black_start is not None:
            assert (
                new_model.to_api()["black_s_start"]
                == envoy.data.dry_contact_settings["NC1"].black_start
            )
        else:
            assert "black_s_start" not in new_model.to_api()

        await envoy.open_dry_contact("NC1")
        assert envoy.data.dry_contact_status["NC1"].status == DryContactStatus.OPEN
        assert respx.calls.last.request.content == orjson.dumps(
            {"dry_contacts": {"id": "NC1", "status": "open"}}
        )

        await envoy.close_dry_contact("NC1")
        assert envoy.data.dry_contact_status["NC1"].status == DryContactStatus.CLOSED
        assert respx.calls.last.request.content == orjson.dumps(
            {"dry_contacts": {"id": "NC1", "status": "closed"}}
        )

        assert "Sending POST" in caplog.text

        # test error returned by action methods calling _json_request
        respx.post(URL_GRID_RELAY).mock(return_value=Response(300, json={}))
        with pytest.raises(EnvoyError):
            await envoy.go_on_grid()
        with pytest.raises(EnvoyError):
            await envoy.go_off_grid()

        respx.post(URL_GRID_RELAY).mock(
            return_value=Response(200, json={})
        ).side_effect = httpx.ConnectError("Test Connection error")
        with pytest.raises(EnvoyError):
            await envoy.go_on_grid()
        respx.post(URL_GRID_RELAY).mock(
            return_value=Response(200, json={})
        ).side_effect = httpx.TimeoutException("Test timeout exception")
        with pytest.raises(EnvoyError):
            await envoy.go_off_grid()

        respx.post(URL_DRY_CONTACT_SETTINGS).mock(return_value=Response(300, json={}))
        with pytest.raises(EnvoyError):
            await envoy.update_dry_contact(new_data)

        respx.post(URL_DRY_CONTACT_STATUS).mock(
            return_value=Response(200, json={})
        ).side_effect = httpx.ConnectError("Test Connection error")
        with pytest.raises(EnvoyError):
            await envoy.close_dry_contact("NC1")

        respx.post(URL_DRY_CONTACT_STATUS).mock(
            return_value=Response(200, json={})
        ).side_effect = httpx.TimeoutException("Test timeout exception")
        with pytest.raises(EnvoyError):
            await envoy.open_dry_contact("NC1")

    else:
        with pytest.raises(EnvoyFeatureNotAvailable):
            await envoy.go_off_grid()
        with pytest.raises(EnvoyFeatureNotAvailable):
            await envoy.go_on_grid()
        with pytest.raises(EnvoyFeatureNotAvailable):
            await envoy.update_dry_contact({"id": "NC1"})
        with pytest.raises(EnvoyFeatureNotAvailable):
            await envoy.update_dry_contact({"id": "NC1"})
        with pytest.raises(EnvoyFeatureNotAvailable):
            await envoy.open_dry_contact("NC1")
        with pytest.raises(EnvoyFeatureNotAvailable):
            await envoy.close_dry_contact("NC1")

    if supported_features & SupportedFeatures.GENERATOR:
        # COV ensemble ENDPOINT_PROBE_EXCEPTIONS
        respx.get("/ivp/ss/gen_config").mock(
            return_value=Response(
                500, json=load_json_fixture(version, "ivp_ss_gen_config")
            )
        )
        await envoy.probe()
        # restore from prior changes
        respx.get("/ivp/ss/gen_config").mock(
            return_value=Response(
                200, json=load_json_fixture(version, "ivp_ss_gen_config")
            )
        )
        await envoy.probe()

    if (supported_features & SupportedFeatures.ENCHARGE) and (
        supported_features & SupportedFeatures.TARIFF
    ):
        # Test `savings-mode` is converted to `economy`
        if (
            envoy.data.raw[URL_TARIFF]["tariff"]["storage_settings"]["mode"]
            == "savings-mode"
        ):
            assert envoy.data.tariff.storage_settings.mode == EnvoyStorageMode.SAVINGS

        storage_settings = envoy.data.tariff.storage_settings
        new_data = {"charge_from_grid": True}
        new_model = replace(storage_settings, **new_data)

        if envoy.data.tariff.storage_settings.date is not None:
            assert new_model.to_api()["date"] == envoy.data.tariff.storage_settings.date
        else:
            assert "date" not in new_model.to_api()

        # Test setting battery features
        await envoy.enable_charge_from_grid()
        assert envoy.data.tariff.storage_settings.charge_from_grid is True
        assert respx.calls.last.request.content == orjson.dumps(
            {"tariff": envoy.data.tariff.to_api()}
        )

        await envoy.disable_charge_from_grid()
        assert envoy.data.tariff.storage_settings.charge_from_grid is False
        assert respx.calls.last.request.content == orjson.dumps(  # type: ignore[unreachable]
            {"tariff": envoy.data.tariff.to_api()}
        )

        await envoy.set_reserve_soc(50)
        assert envoy.data.tariff.storage_settings.reserved_soc == round(float(50), 1)
        assert respx.calls.last.request.content == orjson.dumps(
            {"tariff": envoy.data.tariff.to_api()}
        )

        await envoy.set_storage_mode(EnvoyStorageMode.SELF_CONSUMPTION)
        assert (
            envoy.data.tariff.storage_settings.mode == EnvoyStorageMode.SELF_CONSUMPTION
        )
        assert respx.calls.last.request.content == orjson.dumps(
            {"tariff": envoy.data.tariff.to_api()}
        )

        with pytest.raises(TypeError):
            await envoy.set_storage_mode("invalid")

        # test error returned by action methods calling _json_request
        respx.put(URL_TARIFF).mock(return_value=Response(300, json={}))
        with pytest.raises(EnvoyError):
            await envoy.enable_charge_from_grid()
        respx.put(URL_TARIFF).mock(return_value=Response(200, json={})).side_effect = (
            httpx.TimeoutException("Test timeout exception")
        )
        with pytest.raises(EnvoyError):
            await envoy.disable_charge_from_grid()
        respx.put(URL_TARIFF).mock(return_value=Response(200, json={})).side_effect = (
            httpx.ConnectError("Test Connection error")
        )
        with pytest.raises(EnvoyError):
            await envoy.set_storage_mode(EnvoyStorageMode.SELF_CONSUMPTION)
        respx.put(URL_TARIFF).mock(return_value=Response(200, json={})).side_effect = (
            httpx.ConnectError("Test Connection error")
        )
        with pytest.raises(EnvoyError):
            await envoy.set_reserve_soc(50)

        # COV test with missing logger
        json_data = load_json_fixture(version, "admin_lib_tariff")
        del json_data["tariff"]["logger"]
        respx.get("/admin/lib/tariff").mock(return_value=Response(200, json=json_data))
        respx.put("/admin/lib/tariff").mock(return_value=Response(200, json=json_data))
        await envoy.update()
        envoy.data.tariff.to_api()

        # COV test with missing date for tariff and storage settings
        json_data = load_json_fixture(version, "admin_lib_tariff")
        del json_data["tariff"]["date"]
        del json_data["tariff"]["storage_settings"]["date"]
        respx.get("/admin/lib/tariff").mock(return_value=Response(200, json=json_data))
        respx.put("/admin/lib/tariff").mock(return_value=Response(200, json=json_data))
        await envoy.update()
        envoy.data.tariff.to_api()

        # COV test with missing storage settings
        json_data = load_json_fixture(version, "admin_lib_tariff")
        del json_data["tariff"]["storage_settings"]
        respx.get("/admin/lib/tariff").mock(return_value=Response(200, json=json_data))
        respx.put("/admin/lib/tariff").mock(return_value=Response(200, json=json_data))
        await envoy.update()
        envoy.data.tariff.to_api()

        # COV test with error in result
        json_data = load_json_fixture(version, "admin_lib_tariff")
        json_data.update({"error": "error"})
        respx.get("/admin/lib/tariff").mock(return_value=Response(200, json=json_data))
        try:
            await envoy.probe()
        except AttributeError:
            assert "No tariff data found" in caplog.text

        # COV test with no enpower features
        json_data = load_json_fixture(version, "ivp_ensemble_inventory")
        json_data[0]["type"] = "NOEXCHARGE"
        respx.get("/ivp/ensemble/inventory").mock(
            return_value=Response(200, json=json_data)
        )
        await envoy.probe()
        await envoy.update()

        # COV ensemble ENDPOINT_PROBE_EXCEPTIONS
        respx.get("/ivp/ensemble/inventory").mock(
            return_value=Response(
                500, json=load_json_fixture(version, "ivp_ensemble_inventory")
            )
        )
        await envoy.probe()

        # restore from prior changes
        respx.get("/ivp/ensemble/inventory").mock(
            return_value=Response(
                200, json=load_json_fixture(version, "ivp_ensemble_inventory")
            )
        )
        json_data = load_json_fixture(version, "admin_lib_tariff")
        respx.get("/admin/lib/tariff").mock(return_value=Response(200, json=json_data))

        bad_envoy = await get_mock_envoy()
        await bad_envoy.probe()
        with pytest.raises(EnvoyFeatureNotAvailable):
            bad_envoy.data.tariff.storage_settings = None
            await bad_envoy.enable_charge_from_grid()
        with pytest.raises(ValueError):
            bad_envoy.data.tariff = None
            await bad_envoy.enable_charge_from_grid()
        with pytest.raises(ValueError):
            bad_envoy.data = None
            await bad_envoy.enable_charge_from_grid()
    else:
        with pytest.raises(EnvoyFeatureNotAvailable):
            await envoy.enable_charge_from_grid()
        with pytest.raises(EnvoyFeatureNotAvailable):
            await envoy.disable_charge_from_grid()

    assert envoy.phase_count == phase_count
    assert envoy.ct_meter_count == common_properties["ctMeters"]
    assert envoy.phase_count == common_properties["phaseCount"]
    assert envoy.phase_mode == common_properties["phaseMode"]
    assert envoy.consumption_meter_type == common_properties["consumptionMeter"]
    assert envoy.production_meter_type == common_properties["productionMeter"]
    assert envoy.storage_meter_type == common_properties["storageMeter"]

    # are CT types represented correctly in model
    assert (str(envoy.storage_meter_type) in envoy.envoy_model) != (
        envoy.storage_meter_type is None
    )
    assert (str(envoy.production_meter_type) in envoy.envoy_model) != (
        envoy.production_meter_type is None
    )
    assert (str(envoy.storage_meter_type) in envoy.envoy_model) != (
        envoy.storage_meter_type is None
    )

    # data is the original collected envoy.data
    # are all production phases reported
    expected_phases = production_phases == {}
    actual_phases = data.system_production_phases is None
    assert not (expected_phases ^ actual_phases)

    # are all consumption phases reported
    expected_phases = consumption_phases == {}
    actual_phases = data.system_consumption_phases is None
    assert not (expected_phases ^ actual_phases)

    reported_phase_count = envoy.active_phase_count
    # are all production phases reported
    expected_phase_count = len(production_phases)
    assert expected_phase_count == reported_phase_count

    # are all consumption phases reported
    expected_phase_count = len(consumption_phases)
    assert expected_phase_count == reported_phase_count

    # Test each production phase
    for phase in production_phases:
        proddata = envoy.data.system_production_phases[phase]
        modeldata = production_phases[phase]

        # test each element of the phase data
        assert proddata.watt_hours_lifetime == modeldata["watt_hours_lifetime"]
        assert proddata.watt_hours_last_7_days == modeldata["watt_hours_last_7_days"]
        assert proddata.watt_hours_today == modeldata["watt_hours_today"]
        assert proddata.watts_now == modeldata["watts_now"]

    # are all consumption phases reported
    assert (
        envoy.active_phase_count == 0
        if data.system_consumption_phases is None
        else len(data.system_consumption_phases)
    )
    # Test each consumption phase
    for phase in consumption_phases:
        consdata = envoy.data.system_consumption_phases[phase]
        modeldata = consumption_phases[phase]

        # test each element of the phase data
        assert consdata.watt_hours_lifetime == modeldata["watt_hours_lifetime"]
        assert consdata.watt_hours_last_7_days == modeldata["watt_hours_last_7_days"]
        assert consdata.watt_hours_today == modeldata["watt_hours_today"]
        assert consdata.watts_now == modeldata["watts_now"]

    # test ct production meter values
    for key in ct_production:
        assert ct_production[key] == getattr(envoy.data.ctmeter_production, key)

    # are all CT production phases reported
    assert (
        len(ct_production_phases) == 0
        if data.ctmeter_production_phases is None
        else len(data.ctmeter_production_phases)
    )

    # Test each ct production phase
    for phase in ct_production_phases:
        proddata = envoy.data.ctmeter_production_phases[phase]
        modeldata = ct_production_phases[phase]
        # test each element of the phase data
        for key in modeldata:
            assert modeldata[key] == getattr(proddata, key)

    # test ct consumption meter values
    for key in ct_consumption:
        assert ct_consumption[key] == getattr(envoy.data.ctmeter_consumption, key)

    # are all consumption CT phases reported
    assert (
        len(ct_consumption_phases) == 0
        if data.ctmeter_consumption_phases is None
        else len(data.ctmeter_consumption_phases)
    )

    # Test each ct consumption phase
    for phase in ct_consumption_phases:
        consdata = envoy.data.ctmeter_consumption_phases[phase]
        modeldata = ct_consumption_phases[phase]
        # test each element of the phase data
        for key in modeldata:
            assert modeldata[key] == getattr(consdata, key)

    # test ct storage meter values
    for key in ct_storage:
        assert ct_storage[key] == getattr(envoy.data.ctmeter_storage, key)

    # test expected vs actual phases reported
    assert (
        len(ct_storage_phases) == 0
        if data.ctmeter_storage_phases is None
        else len(data.ctmeter_storage_phases)
    )

    # Test each ct storage phase
    for phase in ct_storage_phases:
        storedata = envoy.data.ctmeter_storage_phases[phase]
        modeldata = ct_storage_phases[phase]
        # test each element of the phase data
        for key in modeldata:
            assert modeldata[key] == getattr(storedata, key)

    # COV test with no production segment
    if "production" in files:
        try:
            json_data = load_json_fixture(version, "production")
        except json.decoder.JSONDecodeError:
            json_data = None
        if json_data:
            del json_data["production"]
        respx.get("/production").mock(return_value=Response(200, json=json_data))
    else:
        respx.get("/production").mock(return_value=Response(404))
    try:
        await envoy.probe()
    except EnvoyProbeFailed:
        pass
