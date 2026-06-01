from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EcejGasApi, EcejGasApiError, EcejGasAuthError
from .cities import CITY_OPTIONS, city_name_from_id
from .const import (
    CONF_CARDBIND_ID,
    CONF_CITY_ID,
    CONF_CITY_NAME,
    CONF_PLATFORM_ONLY_CARD_NO,
    CONF_TOKEN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_CITY_ID,
    DEFAULT_CITY_NAME,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
)

CONF_CITY_SEARCH = "city_search"
CONF_CITY_CHOICE = "city_choice"
CONF_ACCOUNT_CHOICE = "account_choice"


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _city_label(city: dict[str, str]) -> str:
    return f"{city['name']}（cityId: {city['id']}）"


def _city_matches(keyword: str) -> list[dict[str, str]]:
    text = _norm(keyword) or _norm(DEFAULT_CITY_NAME)
    exact: list[dict[str, str]] = []
    partial: list[dict[str, str]] = []

    for city in CITY_OPTIONS:
        fields = (
            _norm(city["name"]),
            _norm(city["id"]),
            _norm(city["pinyin"]),
            _norm(city["city_no"]),
        )
        if text in fields:
            exact.append(city)
        elif any(text in field for field in fields):
            partial.append(city)

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for city in exact + partial:
        if city["id"] not in seen:
            result.append(city)
            seen.add(city["id"])
    return result


def _city_by_name(city_name: str) -> dict[str, str] | None:
    for city in CITY_OPTIONS:
        if city["name"] == city_name:
            return city
    return None


def _choice_selector(cities: list[dict[str, str]]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[{"value": city["name"], "label": _city_label(city)} for city in cities],
            mode=selector.SelectSelectorMode.DROPDOWN,
            custom_value=False,
        )
    )


def _cardbind_id(card: dict[str, Any]) -> str:
    return str(card.get("cardbindID") or card.get("cardbindId") or "")


def _platform_card_no(card: dict[str, Any]) -> str:
    return str(card.get("platformCardNo") or "")


def _account_value(card: dict[str, Any]) -> str:
    return f"{_cardbind_id(card)}|{_platform_card_no(card)}"


def _account_label(card: dict[str, Any]) -> str:
    name = card.get("familyAccounName") or card.get("familyAccountName") or card.get("userName") or "燃气户号"
    pay_no = card.get("payNo") or ""
    address = card.get("address") or ""
    meter_type = card.get("businessName") or ""
    parts = [str(name)]
    if pay_no:
        parts.append(f"缴费号 {pay_no}")
    if meter_type:
        parts.append(str(meter_type))
    if address:
        parts.append(str(address))
    return " / ".join(parts)


def _account_selector(cards: list[dict[str, Any]]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[{"value": _account_value(card), "label": _account_label(card)} for card in cards],
            mode=selector.SelectSelectorMode.DROPDOWN,
            custom_value=False,
        )
    )


def _select_card(cards: list[dict[str, Any]], value: str) -> dict[str, Any] | None:
    for card in cards:
        if _account_value(card) == value:
            return card
    return None


async def _fetch_cards(hass: HomeAssistant, data: dict[str, Any]) -> list[dict[str, Any]]:
    api = EcejGasApi(
        async_get_clientsession(hass),
        str(data[CONF_TOKEN]),
        str(data.get(CONF_CITY_ID, DEFAULT_CITY_ID)),
    )
    return await api.async_get_bind_cards()


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    api = EcejGasApi(
        async_get_clientsession(hass),
        str(data[CONF_TOKEN]),
        str(data.get(CONF_CITY_ID, DEFAULT_CITY_ID)),
        str(data.get(CONF_CARDBIND_ID, "")),
        str(data.get(CONF_PLATFORM_ONLY_CARD_NO, "")),
    )
    return await api.async_get_data()


def _search_schema(defaults: dict[str, Any] | None = None, include_interval: bool = False) -> vol.Schema:
    defaults = defaults or {}
    city_name = defaults.get(CONF_CITY_NAME) or city_name_from_id(
        str(defaults.get(CONF_CITY_ID, DEFAULT_CITY_ID)), DEFAULT_CITY_NAME
    )
    schema: dict[Any, Any] = {
        vol.Required(CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")): str,
        vol.Required(CONF_CITY_SEARCH, default=defaults.get(CONF_CITY_SEARCH, city_name)): str,
    }
    if include_interval:
        schema[
            vol.Required(
                CONF_UPDATE_INTERVAL,
                default=defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES),
            )
        ] = vol.All(vol.Coerce(int), vol.Range(min=10, max=10080))
    return vol.Schema(schema)


def _choice_schema(cities: list[dict[str, str]], default_city: str) -> vol.Schema:
    if not any(city["name"] == default_city for city in cities):
        default_city = cities[0]["name"] if cities else DEFAULT_CITY_NAME
    return vol.Schema({vol.Required(CONF_CITY_CHOICE, default=default_city): _choice_selector(cities)})


def _account_schema(cards: list[dict[str, Any]], default_value: str | None = None) -> vol.Schema:
    values = {_account_value(card) for card in cards}
    if default_value not in values:
        default_value = _account_value(cards[0]) if cards else ""
    return vol.Schema({vol.Required(CONF_ACCOUNT_CHOICE, default=default_value): _account_selector(cards)})


class EcejGasConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 6

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}
        self._matches: list[dict[str, str]] = []
        self._cards: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            matches = _city_matches(str(user_input.get(CONF_CITY_SEARCH) or DEFAULT_CITY_NAME))
            if matches:
                self._pending = dict(user_input)
                self._matches = matches
                return await self.async_step_select_city()
            errors[CONF_CITY_SEARCH] = "invalid_city"

        return self.async_show_form(
            step_id="user",
            data_schema=_search_schema(user_input),
            errors=errors,
        )

    async def async_step_select_city(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if not self._matches:
            return await self.async_step_user()

        default_city = DEFAULT_CITY_NAME
        keyword = _norm(self._pending.get(CONF_CITY_SEARCH, DEFAULT_CITY_NAME))
        for city in self._matches:
            if keyword == _norm(city["name"]):
                default_city = city["name"]
                break

        if user_input is not None:
            city_name = str(user_input.get(CONF_CITY_CHOICE) or "").strip()
            allowed = {city["name"] for city in self._matches}
            city = _city_by_name(city_name)

            if city is None or city_name not in allowed:
                errors[CONF_CITY_CHOICE] = "invalid_city"
            else:
                data = dict(self._pending)
                data.pop(CONF_CITY_SEARCH, None)
                data[CONF_CITY_NAME] = city["name"]
                data[CONF_CITY_ID] = city["id"]
                data[CONF_UPDATE_INTERVAL] = DEFAULT_UPDATE_INTERVAL_MINUTES

                try:
                    self._cards = await _fetch_cards(self.hass, data)
                except EcejGasAuthError:
                    errors["base"] = "auth"
                except EcejGasApiError:
                    errors["base"] = "cannot_connect"
                else:
                    self._pending = data
                    if len(self._cards) > 1:
                        return await self.async_step_select_account()
                    return await self._create_entry_for_card(self._cards[0])

        return self.async_show_form(
            step_id="select_city",
            data_schema=_choice_schema(self._matches, default_city),
            errors=errors,
            description_placeholders={"count": str(len(self._matches))},
        )

    async def async_step_select_account(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if not self._cards:
            return await self.async_step_user()

        if user_input is not None:
            card = _select_card(self._cards, str(user_input.get(CONF_ACCOUNT_CHOICE) or ""))
            if card is None:
                errors[CONF_ACCOUNT_CHOICE] = "invalid_account"
            else:
                return await self._create_entry_for_card(card)

        return self.async_show_form(
            step_id="select_account",
            data_schema=_account_schema(self._cards),
            errors=errors,
            description_placeholders={"count": str(len(self._cards))},
        )

    async def _create_entry_for_card(self, card: dict[str, Any]):
        data = dict(self._pending)
        data[CONF_CARDBIND_ID] = _cardbind_id(card)
        data[CONF_PLATFORM_ONLY_CARD_NO] = _platform_card_no(card)

        try:
            gas_data = await _validate_input(self.hass, data)
        except EcejGasAuthError:
            return self.async_abort(reason="auth")
        except EcejGasApiError:
            return self.async_abort(reason="cannot_connect")

        pay_no = str(gas_data.get("pay_no") or card.get("payNo") or "").strip()
        title = f"新奥燃气 {pay_no}" if pay_no else "新奥燃气"
        await self.async_set_unique_id(f"{data[CONF_CITY_ID]}_{data[CONF_CARDBIND_ID]}")
        self._abort_if_unique_id_configured(updates=data)
        return self.async_create_entry(title=title, data=data)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return EcejGasOptionsFlow(config_entry)


class EcejGasOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        self._pending: dict[str, Any] = {}
        self._matches: list[dict[str, str]] = []
        self._cards: list[dict[str, Any]] = []

    def _current(self) -> dict[str, Any]:
        entry = self._entry
        city_id = entry.options.get(CONF_CITY_ID, entry.data.get(CONF_CITY_ID, DEFAULT_CITY_ID))
        city_name = entry.options.get(
            CONF_CITY_NAME,
            entry.data.get(CONF_CITY_NAME, city_name_from_id(str(city_id), DEFAULT_CITY_NAME)),
        )
        return {
            CONF_TOKEN: entry.options.get(CONF_TOKEN, entry.data.get(CONF_TOKEN, "")),
            CONF_CITY_ID: city_id,
            CONF_CITY_NAME: city_name,
            CONF_CITY_SEARCH: city_name,
            CONF_CARDBIND_ID: entry.options.get(CONF_CARDBIND_ID, entry.data.get(CONF_CARDBIND_ID, "")),
            CONF_PLATFORM_ONLY_CARD_NO: entry.options.get(
                CONF_PLATFORM_ONLY_CARD_NO,
                entry.data.get(CONF_PLATFORM_ONLY_CARD_NO, ""),
            ),
            CONF_UPDATE_INTERVAL: entry.options.get(
                CONF_UPDATE_INTERVAL,
                entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES),
            ),
        }

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        current = self._current()

        if user_input is not None:
            matches = _city_matches(str(user_input.get(CONF_CITY_SEARCH) or DEFAULT_CITY_NAME))
            if matches:
                self._pending = dict(user_input)
                self._matches = matches
                return await self.async_step_select_city()
            errors[CONF_CITY_SEARCH] = "invalid_city"

        return self.async_show_form(
            step_id="init",
            data_schema=_search_schema(user_input or current, include_interval=True),
            errors=errors,
        )

    async def async_step_select_city(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if not self._matches:
            return await self.async_step_init()

        current = self._current()
        default_city = current.get(CONF_CITY_NAME, DEFAULT_CITY_NAME)
        keyword = _norm(self._pending.get(CONF_CITY_SEARCH, default_city))
        for city in self._matches:
            if keyword == _norm(city["name"]):
                default_city = city["name"]
                break

        if user_input is not None:
            city_name = str(user_input.get(CONF_CITY_CHOICE) or "").strip()
            allowed = {city["name"] for city in self._matches}
            city = _city_by_name(city_name)

            if city is None or city_name not in allowed:
                errors[CONF_CITY_CHOICE] = "invalid_city"
            else:
                data = dict(self._pending)
                data.pop(CONF_CITY_SEARCH, None)
                data[CONF_CITY_NAME] = city["name"]
                data[CONF_CITY_ID] = city["id"]

                try:
                    self._cards = await _fetch_cards(self.hass, data)
                except EcejGasAuthError:
                    errors["base"] = "auth"
                except EcejGasApiError:
                    errors["base"] = "cannot_connect"
                else:
                    self._pending = data
                    if len(self._cards) > 1:
                        return await self.async_step_select_account()
                    return await self._create_options_for_card(self._cards[0])

        return self.async_show_form(
            step_id="select_city",
            data_schema=_choice_schema(self._matches, default_city),
            errors=errors,
            description_placeholders={"count": str(len(self._matches))},
        )

    async def async_step_select_account(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if not self._cards:
            return await self.async_step_init()

        current = self._current()
        current_value = f"{current.get(CONF_CARDBIND_ID, '')}|{current.get(CONF_PLATFORM_ONLY_CARD_NO, '')}"

        if user_input is not None:
            card = _select_card(self._cards, str(user_input.get(CONF_ACCOUNT_CHOICE) or ""))
            if card is None:
                errors[CONF_ACCOUNT_CHOICE] = "invalid_account"
            else:
                return await self._create_options_for_card(card)

        return self.async_show_form(
            step_id="select_account",
            data_schema=_account_schema(self._cards, current_value),
            errors=errors,
            description_placeholders={"count": str(len(self._cards))},
        )

    async def _create_options_for_card(self, card: dict[str, Any]):
        data = dict(self._pending)
        data[CONF_CARDBIND_ID] = _cardbind_id(card)
        data[CONF_PLATFORM_ONLY_CARD_NO] = _platform_card_no(card)

        try:
            await _validate_input(self.hass, data)
        except EcejGasAuthError:
            return self.async_abort(reason="auth")
        except EcejGasApiError:
            return self.async_abort(reason="cannot_connect")

        return self.async_create_entry(title="", data=data)
