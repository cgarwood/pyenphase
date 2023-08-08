import logging

from awesomeversion import AwesomeVersion

from ..const import URL_PRODUCTION_V1, SupportedFeatures
from ..exceptions import ENDPOINT_PROBE_EXCEPTIONS
from ..models.envoy import EnvoyData
from ..models.system_production import EnvoySystemProduction
from .base import EnvoyUpdater

_LOGGER = logging.getLogger(__name__)


class EnvoyApiV1ProductionUpdater(EnvoyUpdater):
    """Class to handle updates for production data."""

    def should_probe(
        self, envoy_version: AwesomeVersion, discovered_features: SupportedFeatures
    ) -> bool:
        """Return True if this updater should be probed."""
        return SupportedFeatures.PRODUCTION not in discovered_features

    async def probe(self) -> SupportedFeatures | None:
        """Probe the Envoy for this updater and return SupportedFeatures."""
        try:
            await self._json_probe_request(URL_PRODUCTION_V1)
        except ENDPOINT_PROBE_EXCEPTIONS as e:
            _LOGGER.debug(
                "Production endpoint not found at %s: %s", URL_PRODUCTION_V1, e
            )
            return None
        self._supported_features |= SupportedFeatures.PRODUCTION
        return self._supported_features

    async def update(self, envoy_data: EnvoyData) -> None:
        """Update the Envoy for this updater."""
        production_data = await self._json_request(URL_PRODUCTION_V1)
        envoy_data.raw[URL_PRODUCTION_V1] = production_data
        envoy_data.system_production = EnvoySystemProduction.from_v1_api(
            production_data
        )
