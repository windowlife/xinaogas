from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EcejGasApi, EcejGasApiError
from .const import (
    CONF_CITY_ID,
    CONF_CITY_NAME,
    CONF_TOKEN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_CITY_ID,
    DEFAULT_CITY_NAME,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class EcejGasCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.api = EcejGasApi(
            async_get_clientsession(hass),
            self._get_value(entry, CONF_TOKEN),
            self._get_value(entry, CONF_CITY_ID, DEFAULT_CITY_ID),
            self._get_value(entry, "cardbind_id"),
            self._get_value(entry, "platform_only_card_no"),
        )
        update_minutes = int(
            entry.options.get(
                CONF_UPDATE_INTERVAL,
                entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES),
            )
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=update_minutes),
            always_update=True,
        )

    @staticmethod
    def _get_value(entry: ConfigEntry, key: str, default: str | None = None) -> str:
        value = entry.options.get(key, entry.data.get(key, default))
        return "" if value is None else str(value)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.api.async_get_data()
        except EcejGasApiError as err:
            raise UpdateFailed(str(err)) from err
        data["configured_city_name"] = self._get_value(self.entry, CONF_CITY_NAME, DEFAULT_CITY_NAME)
        return data
