from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATORS, DATA_TOKEN_MANAGER, DOMAIN
from .coordinator import XinaoGasCoordinator
from .token_manager import XinaoGasTokenManager

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    domain_data = hass.data.setdefault(DOMAIN, {})
    token_manager: XinaoGasTokenManager | None = domain_data.get(DATA_TOKEN_MANAGER)
    if token_manager is None:
        token_manager = XinaoGasTokenManager(hass)
        await token_manager.async_load()
        domain_data[DATA_TOKEN_MANAGER] = token_manager

    legacy_token = str(entry.data.get("token") or entry.options.get("token") or "").strip()
    legacy_ttl = entry.data.get("token_ttl") or entry.options.get("token_ttl")
    await token_manager.async_import_entry_token(legacy_token, int(legacy_ttl or 0) or None)
    token_manager.async_schedule()

    coordinator = XinaoGasCoordinator(hass, entry, token_manager)
    await coordinator.async_config_entry_first_refresh()

    payment_no = str((coordinator.data or {}).get("payment_no") or "").strip()
    title = f"新奥燃气 {payment_no}" if payment_no else "新奥燃气"
    if entry.title != title:
        hass.config_entries.async_update_entry(entry, title=title)

    coordinators = domain_data.setdefault(DATA_COORDINATORS, {})
    coordinators[entry.entry_id] = coordinator
    domain_data[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data.get(DOMAIN, {})
        domain_data.pop(entry.entry_id, None)
        coordinators = domain_data.get(DATA_COORDINATORS, {})
        coordinators.pop(entry.entry_id, None)
        if not coordinators:
            token_manager: XinaoGasTokenManager | None = domain_data.get(DATA_TOKEN_MANAGER)
            if token_manager:
                token_manager.async_cancel()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
