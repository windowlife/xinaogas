from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .api import (
    XinaoGasApiError,
    XinaoGasAuthError,
    contract_no,
    payment_no,
    platform_card_no,
)
from .const import (
    CONF_CONTRACT_NO,
    CONF_PAYMENT_NO,
    CONF_PLATFORM_CARD_NO,
    CONF_TOKEN,
    CONF_UPDATE_INTERVAL,
    DATA_TOKEN_MANAGER,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
)
from .token_manager import XinaoGasTokenManager

CONF_ACCOUNT_CHOICE = "account_choice"


def _card_value(card: dict[str, Any]) -> str:
    return "|".join((platform_card_no(card), contract_no(card), payment_no(card)))


def _card_label(card: dict[str, Any]) -> str:
    pno = payment_no(card) or "燃气户号"
    card_type = card.get("cardType") or card.get("businessName") or card.get("businessTypeName") or ""
    address = card.get("address") or ""
    company = card.get("companyName") or ""
    parts = [f"新奥燃气 {pno}"]
    for item in (card_type, address, company):
        if item:
            parts.append(str(item))
    return " / ".join(parts)


def _account_schema(cards: list[dict[str, Any]], default_value: str | None = None) -> vol.Schema:
    values = {_card_value(card) for card in cards}
    if default_value not in values:
        default_value = _card_value(cards[0]) if cards else ""
    return vol.Schema(
        {
            vol.Required(CONF_ACCOUNT_CHOICE, default=default_value): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"value": _card_value(card), "label": _card_label(card)} for card in cards],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    custom_value=False,
                )
            )
        }
    )


def _select_card(cards: list[dict[str, Any]], value: str) -> dict[str, Any] | None:
    for card in cards:
        if _card_value(card) == value:
            return card
    return None


def _user_schema(defaults: dict[str, Any] | None = None, require_token: bool = True) -> vol.Schema:
    defaults = defaults or {}
    token_default = defaults.get(CONF_TOKEN, "")
    token_field = vol.Required(CONF_TOKEN, default=token_default) if require_token else vol.Optional(CONF_TOKEN, default=token_default)
    return vol.Schema(
        {
            token_field: str,
            vol.Required(
                CONF_UPDATE_INTERVAL,
                default=defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES),
            ): vol.All(vol.Coerce(int), vol.Range(min=60, max=10080)),
        }
    )


async def _get_token_manager(hass: HomeAssistant) -> XinaoGasTokenManager:
    domain_data = hass.data.setdefault(DOMAIN, {})
    manager: XinaoGasTokenManager | None = domain_data.get(DATA_TOKEN_MANAGER)
    if manager is None:
        manager = XinaoGasTokenManager(hass)
        await manager.async_load()
        domain_data[DATA_TOKEN_MANAGER] = manager
    return manager


async def _fetch_cards(hass: HomeAssistant, token: str | None = None) -> list[dict[str, Any]]:
    manager = await _get_token_manager(hass)
    token = (token or "").strip()
    if token:
        await manager.async_set_token(token)
        await manager.async_probe_token()
    elif not await manager.async_get_token():
        raise XinaoGasAuthError("缺少小程序 token")
    else:
        await manager.async_ensure_valid_token()

    api = await manager.async_get_api()
    return await api.async_get_bind_cards()


def _put_card_data(data: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in data.items() if key not in {CONF_TOKEN, "token_ttl", "token_save_time", "token_next_check"}}
    result[CONF_PLATFORM_CARD_NO] = platform_card_no(card)
    result[CONF_CONTRACT_NO] = contract_no(card)
    result[CONF_PAYMENT_NO] = payment_no(card)

    for key in (
        "companyName",
        "address",
        "userName",
        "familyAccountName",
        "familyAccounName",
        "businessType",
        "businessName",
        "businessTypeName",
        "cardType",
        "cityId",
        "cityNo",
        "companyCode",
    ):
        value = card.get(key)
        if value not in (None, "", [], {}):
            result[f"card_{key}"] = value
    return result


class XinaoGasConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 8

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}
        self._cards: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        manager = await _get_token_manager(self.hass)
        has_token = bool(await manager.async_get_token())

        if user_input is None and has_token:
            try:
                cards = await _fetch_cards(self.hass)
            except XinaoGasAuthError:
                errors["base"] = "auth"
                has_token = False
            except XinaoGasApiError:
                errors["base"] = "cannot_connect"
                has_token = False
            else:
                return await self._start_account_selection(cards, DEFAULT_UPDATE_INTERVAL_MINUTES)

        if user_input is not None:
            token = str(user_input.get(CONF_TOKEN) or "").strip()
            try:
                cards = await _fetch_cards(self.hass, token or None)
            except XinaoGasAuthError:
                errors["base"] = "auth"
            except XinaoGasApiError:
                errors["base"] = "cannot_connect"
            else:
                return await self._start_account_selection(
                    cards, user_input.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES)
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input, require_token=not has_token),
            errors=errors,
        )

    async def _start_account_selection(self, cards: list[dict[str, Any]], update_interval: int):
        self._cards = cards
        self._pending = {CONF_UPDATE_INTERVAL: update_interval}
        if not cards:
            raise XinaoGasApiError("未找到燃气户号")
        if len(cards) > 1:
            return await self.async_step_select_account()
        return await self._create_entry(cards[0])

    async def async_step_select_account(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if not self._cards:
            return await self.async_step_user()

        if user_input is not None:
            card = _select_card(self._cards, str(user_input.get(CONF_ACCOUNT_CHOICE) or ""))
            if card is None:
                errors[CONF_ACCOUNT_CHOICE] = "invalid_account"
            else:
                return await self._create_entry(card)

        return self.async_show_form(
            step_id="select_account",
            data_schema=_account_schema(self._cards),
            errors=errors,
            description_placeholders={"count": str(len(self._cards))},
        )

    async def _create_entry(self, card: dict[str, Any]):
        data = _put_card_data(self._pending, card)
        key = data.get(CONF_PLATFORM_CARD_NO) or data.get(CONF_CONTRACT_NO) or data.get(CONF_PAYMENT_NO)
        await self.async_set_unique_id(str(key))
        self._abort_if_unique_id_configured(updates=data)
        title = f"新奥燃气 {data.get(CONF_PAYMENT_NO)}" if data.get(CONF_PAYMENT_NO) else "新奥燃气"
        return self.async_create_entry(title=title, data=data)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return XinaoGasOptionsFlow(config_entry)


class XinaoGasOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        self._pending: dict[str, Any] = {}
        self._cards: list[dict[str, Any]] = []

    def _current(self) -> dict[str, Any]:
        entry = self._entry
        return {
            CONF_TOKEN: "",
            CONF_UPDATE_INTERVAL: entry.options.get(
                CONF_UPDATE_INTERVAL,
                entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES),
            ),
            CONF_PLATFORM_CARD_NO: entry.options.get(CONF_PLATFORM_CARD_NO, entry.data.get(CONF_PLATFORM_CARD_NO, "")),
            CONF_CONTRACT_NO: entry.options.get(CONF_CONTRACT_NO, entry.data.get(CONF_CONTRACT_NO, "")),
            CONF_PAYMENT_NO: entry.options.get(CONF_PAYMENT_NO, entry.data.get(CONF_PAYMENT_NO, "")),
        }

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        current = self._current()
        manager = await _get_token_manager(self.hass)
        has_token = bool(await manager.async_get_token())

        if user_input is not None:
            token = str(user_input.get(CONF_TOKEN) or "").strip()
            try:
                cards = await _fetch_cards(self.hass, token or None)
            except XinaoGasAuthError:
                errors["base"] = "auth"
            except XinaoGasApiError:
                errors["base"] = "cannot_connect"
            else:
                self._cards = cards
                self._pending = {CONF_UPDATE_INTERVAL: user_input.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES)}
                if len(cards) > 1:
                    return await self.async_step_select_account()
                return await self._create_options(cards[0])

        return self.async_show_form(
            step_id="init",
            data_schema=_user_schema(user_input or current, require_token=not has_token),
            errors=errors,
        )

    async def async_step_select_account(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if not self._cards:
            return await self.async_step_init()

        current = self._current()
        current_value = "|".join(
            (
                str(current.get(CONF_PLATFORM_CARD_NO) or ""),
                str(current.get(CONF_CONTRACT_NO) or ""),
                str(current.get(CONF_PAYMENT_NO) or ""),
            )
        )

        if user_input is not None:
            card = _select_card(self._cards, str(user_input.get(CONF_ACCOUNT_CHOICE) or ""))
            if card is None:
                errors[CONF_ACCOUNT_CHOICE] = "invalid_account"
            else:
                return await self._create_options(card)

        return self.async_show_form(
            step_id="select_account",
            data_schema=_account_schema(self._cards, current_value),
            errors=errors,
            description_placeholders={"count": str(len(self._cards))},
        )

    async def _create_options(self, card: dict[str, Any]):
        return self.async_create_entry(title="", data=_put_card_data(self._pending, card))
