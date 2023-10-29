from abc import abstractmethod
from collections.abc import Awaitable
from typing import Any, Callable

import httpx
from awesomeversion import AwesomeVersion

from ..const import SupportedFeatures
from ..json import json_loads
from ..models.envoy import EnvoyData


class EnvoyUpdater:
    """Base class for Envoy updaters."""

    def __init__(
        self,
        envoy_version: AwesomeVersion,
        probe_request: Callable[[str], Awaitable[httpx.Response]],
        request: Callable[[str], Awaitable[httpx.Response]],
        common_properties: dict[str, Any],
    ) -> None:
        """Initialize the Envoy endpoint."""
        self._envoy_version = envoy_version
        self._probe_request = probe_request
        self._request = request
        self._supported_features = SupportedFeatures(0)
        self._common_properties = common_properties

    async def _json_request(self, end_point: str) -> Any:
        """Make a request to the Envoy and return the JSON response."""
        response = await self._request(end_point)
        return json_loads(end_point, response.content)

    async def _json_probe_request(self, end_point: str) -> Any:
        """Make a probe request to the Envoy and return the JSON response."""
        response = await self._probe_request(end_point)
        return json_loads(end_point, response.content)

    def _add_common_property(self, property: str, property_value: Any) -> None:
        """Add common property to envoy for use by updaters probe"""
        self._common_properties[property] = property_value

    @abstractmethod
    async def probe(
        self, discovered_features: SupportedFeatures
    ) -> SupportedFeatures | None:
        """Probe the Envoy for this updater and return SupportedFeatures."""

    @abstractmethod
    async def update(self, data: EnvoyData) -> None:
        """Update the Envoy for this updater."""
