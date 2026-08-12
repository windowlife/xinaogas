from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import logging
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    XinaoGasApi,
    XinaoGasApiError,
    contract_no,
    first_dict,
    meter_type,
    payment_no,
    platform_card_no,
    to_number,
)
from .const import (
    CONF_CONTRACT_NO,
    CONF_PAYMENT_NO,
    CONF_PLATFORM_CARD_NO,
    CONF_TOKEN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
)
from .token_manager import XinaoGasTokenManager

_LOGGER = logging.getLogger(__name__)

TOKEN_RENEW_AHEAD_MS = 7200000
TOKEN_CLOSE_RENEW_AHEAD_MS = 3600000
TOKEN_RECHECK_INTERVAL_MS = 1800000
TOKEN_RECHECK_CLOSE_INTERVAL_MS = 600000
TOKEN_MIN_SCHEDULE_DELAY_MS = 60000


DAILY_LIST_KEYS = (
    "dailyUsageList",
    "dailyUseList",
    "dayUsageList",
    "monthDailyUsageList",
    "dailyGasList",
    "gasDailyUsageList",
    "usageDetailList",
    "dailyList",
)

DATE_KEYS = (
    "date",
    "day",
    "usageDate",
    "gasDate",
    "readDate",
    "freezeDate",
    "time",
    "rq",
)

USAGE_KEYS = (
    "usage",
    "gasUsage",
    "dayUsage",
    "dailyUsage",
    "dailyGas",
    "usedGas",
    "useGas",
    "gasCount",
    "gasNum",
    "gasVolume",
    "volume",
    "gasValue",
    "dayValue",
    "value",
)


class XinaoGasCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, token_manager: XinaoGasTokenManager) -> None:
        self.entry = entry
        self.token_manager = token_manager
        self.store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
        self.store_data: dict[str, Any] = {}
        self.api = XinaoGasApi(async_get_clientsession(hass), "")
        self._token_unsub: Callable[[], None] | None = None
        minutes = max(60, int(self._entry_value(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES)))
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
            always_update=True,
        )

    def _entry_value(self, key: str, default: Any = "") -> str:
        value = self.entry.options.get(key, self.entry.data.get(key, default))
        return "" if value is None else str(value)

    def _entry_card(self) -> dict[str, Any]:
        source: dict[str, Any] = {}
        source.update(self.entry.data)
        source.update(self.entry.options)
        card: dict[str, Any] = {}
        for key, value in source.items():
            if key.startswith("card_") and value not in (None, "", [], {}):
                card[key[5:]] = value
        return card

    async def _load_store(self) -> None:
        if self.store_data:
            return
        self.store_data = await self.store.async_load() or {}
        legacy_token = str(self.store_data.get("token") or self._entry_value(CONF_TOKEN)).strip()
        legacy_ttl = int(self.store_data.get("token_ttl") or self.entry.data.get("token_ttl") or 0)
        await self.token_manager.async_import_entry_token(legacy_token, legacy_ttl or None)

    def _next_token_check(self, now: int, ttl: int) -> int:
        if ttl <= 0:
            return now + TOKEN_RECHECK_CLOSE_INTERVAL_MS
        if ttl > TOKEN_RENEW_AHEAD_MS:
            return now + ttl - TOKEN_RENEW_AHEAD_MS
        if ttl > TOKEN_CLOSE_RENEW_AHEAD_MS:
            delay = min(TOKEN_RECHECK_INTERVAL_MS, ttl - TOKEN_CLOSE_RENEW_AHEAD_MS)
            return now + max(delay, TOKEN_MIN_SCHEDULE_DELAY_MS)
        if ttl > TOKEN_MIN_SCHEDULE_DELAY_MS * 2:
            delay = min(TOKEN_RECHECK_CLOSE_INTERVAL_MS, ttl - TOKEN_MIN_SCHEDULE_DELAY_MS)
            return now + max(delay, TOKEN_MIN_SCHEDULE_DELAY_MS)
        return now + TOKEN_MIN_SCHEDULE_DELAY_MS

    def async_schedule_token_check(self) -> None:
        if self._token_unsub:
            self._token_unsub()
            self._token_unsub = None

        next_check = int(self.store_data.get("token_next_check") or 0)
        if not next_check:
            return

        now = int(dt_util.utcnow().timestamp() * 1000)
        check_at = max(next_check, now + TOKEN_MIN_SCHEDULE_DELAY_MS)
        when = datetime.fromtimestamp(check_at / 1000, timezone.utc)
        self._token_unsub = async_track_point_in_utc_time(self.hass, self._async_token_check, when)

    def async_cancel_token_check(self) -> None:
        if self._token_unsub:
            self._token_unsub()
            self._token_unsub = None

    async def _async_token_check(self, _now: datetime) -> None:
        self._token_unsub = None
        await self._renew_token()
        self.async_schedule_token_check()

    async def _renew_token(self) -> None:
        await self._load_store()
        now = int(dt_util.utcnow().timestamp() * 1000)
        next_check = int(self.store_data.get("token_next_check") or 0)

        ttl = int(self.store_data.get("token_ttl") or 0)
        save_time = int(self.store_data.get("token_save_time") or 0)
        if ttl and save_time:
            remain = save_time + ttl - now
            if remain > TOKEN_RENEW_AHEAD_MS:
                self.store_data.update({"token_ttl": remain, "token_save_time": now, "token_next_check": self._next_token_check(now, remain)})
                await self.store.async_save(self.store_data)
                return
            check_interval = TOKEN_RECHECK_INTERVAL_MS if remain > TOKEN_CLOSE_RENEW_AHEAD_MS else TOKEN_RECHECK_CLOSE_INTERVAL_MS
            if next_check and now < next_check and next_check - now <= check_interval:
                return

        try:
            data = await self.api.async_apply_token()
        except XinaoGasApiError:
            self.store_data.update({"token_last_check": now, "token_next_check": now + TOKEN_RECHECK_CLOSE_INTERVAL_MS})
            await self.store.async_save(self.store_data)
            return

        ttl = int(data.get("newTokenTtl") or data.get("tokenTtl") or data.get("currentTokenTtl") or ttl or 0)
        self.store_data.update({
            "token": self.api.token,
            "token_ttl": ttl,
            "token_save_time": now,
            "token_last_check": now,
            "token_next_check": self._next_token_check(now, ttl),
        })
        await self.store.async_save(self.store_data)

    def _date_text(self, value: Any) -> str | None:
        if value in (None, ""):
            return None
        text = str(value).strip().replace("年", "-").replace("月", "-").replace("日", "")
        text = text.replace("/", "-").replace(".", "-")
        if len(text) >= 10:
            text = text[:10]
        elif len(text) == 8 and text.isdigit():
            text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        parts = text.split("-")
        if len(parts) == 3 and all(parts):
            text = f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            return None

    def _first_value(self, data: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for key in keys:
            value = data.get(key)
            if value not in (None, ""):
                return value
        return None

    def _find_daily_list(self, data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, dict):
            return []
        for key in DAILY_LIST_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        for value in data.values():
            items = self._find_daily_list(value)
            if items:
                return items
        return []

    def _daily_from_api(self, *sources: dict[str, Any]) -> dict[str, float]:
        daily: dict[str, float] = {}
        for source in sources:
            for item in self._find_daily_list(source):
                day = self._date_text(self._first_value(item, DATE_KEYS))
                usage = to_number(self._first_value(item, USAGE_KEYS))
                if day and usage is not None:
                    daily[day] = round(usage, 3)
        return daily

    def _recent_daily(self, daily: dict[str, Any], days: int = 30) -> dict[str, float]:
        result: dict[str, float] = {}
        for key, value in sorted(daily.items()):
            day = self._date_text(key)
            usage = to_number(value)
            if day and usage is not None:
                result[day] = round(usage, 3)
        return dict(list(result.items())[-days:])

    def _update_usage(self, energy: dict[str, Any], month_total: float | None) -> dict[str, Any]:
        stored = self.store_data.get("daily")
        if not isinstance(stored, dict):
            stored = {}

        api_daily = self._daily_from_api(energy)
        if api_daily:
            min_api_day = min(api_daily)
            kept = {key: value for key, value in stored.items() if self._date_text(key) and self._date_text(key) < min_api_day}
            kept.update(api_daily)
            daily_all = self._recent_daily(kept, 180)
            self.store_data["daily"] = daily_all
            self.store_data["daily_source"] = "energy_analysis"
            self.store_data["last_month_total"] = month_total
        elif self.store_data.get("daily_source") == "energy_analysis":
            daily_all = self._recent_daily(stored, 180)
        else:
            daily_all = {}
            self.store_data["daily"] = {}

        daily_30 = self._recent_daily(daily_all, 30)
        last_day = max(api_daily) if api_daily else (next(reversed(daily_30), None) if daily_30 else None)
        return {
            "daily_usage": daily_all.get(last_day) if last_day else None,
            "daily_usage_date": last_day,
            "daily_usage_map": daily_30,
            "daily_usage_all": daily_all,
        }

    def _after_reading_usage(self, meter_reading_date: Any, daily: dict[str, Any]) -> float:
        reading_date = self._date_text(meter_reading_date)
        total = 0.0
        if reading_date:
            for key, value in daily.items():
                day = self._date_text(key)
                usage = to_number(value)
                if day and day > reading_date and usage is not None:
                    total += usage
        return round(total, 3)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            await self._load_store()
            await self.token_manager.async_ensure_valid_token()
            self.api = await self.token_manager.async_get_api()
            entry_card = self._entry_card()
            card = await self.api.async_get_card(
                self._entry_value(CONF_PLATFORM_CARD_NO),
                self._entry_value(CONF_CONTRACT_NO),
                self._entry_value(CONF_PAYMENT_NO),
            )
            if entry_card:
                merged_card = dict(entry_card)
                merged_card.update({key: value for key, value in card.items() if value not in (None, "", [], {})})
                card = merged_card
            bill: dict[str, Any] = {}
            bill_list: dict[str, Any] = {}
            meter: dict[str, Any] = {}
            energy: dict[str, Any] = {}

            try:
                bill = await self.api.async_get_bill(card)
            except XinaoGasApiError:
                bill = {}

            try:
                bill_list = await self.api.async_get_bill_list(card)
            except XinaoGasApiError:
                bill_list = {}

            try:
                meter = await self.api.async_get_meter_gas_info(card)
            except XinaoGasApiError:
                meter = {}

            try:
                energy = await self.api.async_get_energy_analysis(card)
            except XinaoGasApiError:
                energy = {}

            history = bill_list.get("ordinaryMeterHistoricalBillDtoList")
            latest_bill = first_dict(history) if history else {}
            ladder = first_dict(latest_bill.get("ordinaryMeterLadderList"))
            energy_ladder = first_dict(energy.get("ladderDtoList"))
            meter_reading = to_number(latest_bill.get("thisMeterReading"))
            meter_reading_date = latest_bill.get("meterReadingDate")
            month_total = to_number(energy.get("currentMonthUsage")) or to_number(meter.get("currentMonthTotal"))
            usage_data = self._update_usage(energy, month_total)
            daily_map = usage_data.get("daily_usage_map") or {}
            daily_all = usage_data.get("daily_usage_all") or {}
            after_reading = self._after_reading_usage(meter_reading_date, daily_all)
            cumulative_usage = round(meter_reading + after_reading, 3) if meter_reading is not None else None
            await self.store.async_save(self.store_data)

            return {
                "balance": to_number(meter.get("balance")) or to_number(bill.get("balance")),
                "current_month_usage": to_number(latest_bill.get("thisGasConsumption")),
                "current_month_amount": to_number(latest_bill.get("totalAmount") or ladder.get("total")),
                "meter_reading": meter_reading,
                "last_meter_reading": to_number(latest_bill.get("lastTimeMeterReading")),
                "meter_reading_date": meter_reading_date,
                "latest_bill_date": latest_bill.get("sortDate") or latest_bill.get("statementDate"),
                "gas_price": to_number(ladder.get("gasPrice") or energy_ladder.get("gasPrice") or energy_ladder.get("price")),
                "ladder": ladder.get("jTName") or energy_ladder.get("ladderName") or energy_ladder.get("name"),
                "bill_status": latest_bill.get("status") or "未知",
                "month_total_gas": month_total,
                "daily_usage": usage_data.get("daily_usage"),
                "daily_usage_date": usage_data.get("daily_usage_date"),
                "daily_usage_map": daily_map,
                "after_reading_usage": after_reading,
                "base_meter_reading_time": meter_reading_date,
                "cumulative_usage": cumulative_usage,
                "valve_status": meter.get("valveStatusDesc") or meter.get("valveStatus") or "未知",
                "battery_status": meter.get("batteryStatus") or meter.get("batteryStatusDesc") or "未知",
                "iot_update_time": meter.get("updateTime") or "未知",
                "last_update_time": dt_util.now().strftime("%Y-%m-%d %H:%M:%S"),
                "meter_type": meter_type(card, meter) or "未知",
                "company_name": card.get("companyName") or bill.get("companyName") or "未知",
                "payment_no": payment_no(card),
                "contract_no": contract_no(card),
                "platform_card_no": platform_card_no(card),
                "address": meter.get("address") or card.get("address") or bill.get("address"),
                "user_name": card.get("userName") or bill.get("name"),
                "family_account_name": card.get("familyAccountName") or card.get("familyAccounName") or card.get("tagName"),
                "city_id": card.get("cityId") or bill.get("cityId"),
                "business_type": card.get("businessType"),
                "card_type": card.get("cardType"),
                "raw_meter_gas_info": meter,
                "raw_energy_analysis": energy,
            }
        except XinaoGasApiError as err:
            raise UpdateFailed(str(err)) from err
