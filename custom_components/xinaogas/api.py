from __future__ import annotations

import hashlib
import random
import re
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.util import dt as dt_util

from .const import APPKEY_SECRET, BALANCE_URL, BILL_URL, BIND_CARDS_URL, USER_AGENT


class EcejGasApiError(Exception):
    pass


class EcejGasAuthError(EcejGasApiError):
    pass


def generate_app_key() -> str:
    date_str = dt_util.now().strftime("%Y%m%d%H%M%S")
    value = hashlib.md5((date_str + APPKEY_SECRET).encode("utf-8")).hexdigest()
    return f"{date_str}{value}"


def _to_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).strip())
    return float(match.group(0)) if match else None


def _first(value: Any) -> dict[str, Any]:
    if isinstance(value, list) and value:
        return value[0] if isinstance(value[0], dict) else {}
    return value if isinstance(value, dict) else {}


def _meter_type(card: dict[str, Any]) -> str | None:
    business_name = card.get("businessName")
    if business_name:
        return str(business_name)
    business_type = str(card.get("businessType") or "")
    if business_type == "4":
        return "燃气普表"
    if business_type == "21":
        return "物联表"
    return None


def _card_key(card: dict[str, Any]) -> tuple[str, str]:
    return str(card.get("cardbindID") or card.get("cardbindId") or ""), str(card.get("platformCardNo") or "")


class EcejGasApi:
    def __init__(
        self,
        session: ClientSession,
        token: str,
        city_id: str,
        cardbind_id: str | None = None,
        platform_card_no: str | None = None,
    ) -> None:
        self._session = session
        self._token = token.strip()
        self._city_id = city_id.strip()
        self._cardbind_id = (cardbind_id or "").strip()
        self._platform_card_no = (platform_card_no or "").strip()

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "*/*",
            "Accept-Language": "zh-Hans-CN;q=1.0",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "Host": "lp.ecej.com",
            "OSVersion": "26.4.2",
            "platform": "ios",
            "version": "101387",
            "random": str(random.randint(1000, 9999)),
            "cityId": self._city_id,
            "User-Agent": USER_AGENT,
        }

    def _check(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise EcejGasApiError("接口返回格式异常")

        result_code = str(payload.get("resultCode"))
        message = payload.get("message") or payload.get("msg") or "接口返回失败"

        if result_code == "200":
            return payload
        if result_code in {"401", "403", "1001", "1002", "1003"} or "token" in str(message).lower():
            raise EcejGasAuthError(f"认证失败：{message}")
        raise EcejGasApiError(f"{message}（{result_code}）")

    async def _get(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            async with self._session.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=ClientTimeout(total=25),
            ) as response:
                response.raise_for_status()
                return self._check(await response.json(content_type=None))
        except ClientError as err:
            raise EcejGasApiError(str(err)) from err
        except ValueError as err:
            raise EcejGasApiError("接口返回无法解析") from err

    async def _post(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        try:
            async with self._session.post(
                url,
                headers=self._headers(),
                data=data,
                timeout=ClientTimeout(total=25),
            ) as response:
                response.raise_for_status()
                return self._check(await response.json(content_type=None))
        except ClientError as err:
            raise EcejGasApiError(str(err)) from err
        except ValueError as err:
            raise EcejGasApiError("接口返回无法解析") from err

    async def async_get_bind_cards(self) -> list[dict[str, Any]]:
        payload = await self._get(
            BIND_CARDS_URL,
            {
                "appKey": generate_app_key(),
                "cityId": self._city_id,
                "token": self._token,
            },
        )

        cards: list[tuple[str, dict[str, Any]]] = []
        for business in payload.get("data") or []:
            if not isinstance(business, dict):
                continue
            business_type = str(business.get("businessType") or "")
            business_name = business.get("businessName")
            for card in business.get("cardList") or []:
                if not isinstance(card, dict):
                    continue
                if not card.get("cardbindID") or not card.get("platformCardNo"):
                    continue
                card = dict(card)
                card["businessType"] = card.get("businessType") or business_type
                card["businessName"] = card.get("businessName") or business_name
                cards.append((business_type, card))

        sorted_cards: list[dict[str, Any]] = []
        used: set[tuple[str, str]] = set()
        for business_type in ("4", "21"):
            for item_type, card in cards:
                key = _card_key(card)
                if item_type == business_type and key not in used:
                    sorted_cards.append(card)
                    used.add(key)
        for _, card in cards:
            key = _card_key(card)
            if key not in used:
                sorted_cards.append(card)
                used.add(key)

        if not sorted_cards:
            raise EcejGasApiError("未找到绑定的燃气户号")
        return sorted_cards

    async def async_get_bind_card(self) -> dict[str, Any]:
        cards = await self.async_get_bind_cards()
        if self._cardbind_id or self._platform_card_no:
            for card in cards:
                cardbind_id, platform_card_no = _card_key(card)
                if self._cardbind_id and cardbind_id == self._cardbind_id:
                    return card
                if self._platform_card_no and platform_card_no == self._platform_card_no:
                    return card
        return cards[0]

    async def async_get_balance(self, cardbind_id: str) -> dict[str, Any]:
        return await self._post(
            BALANCE_URL,
            {
                "appKey": generate_app_key(),
                "cardbindId": str(cardbind_id),
                "cityId": self._city_id,
                "token": self._token,
            },
        )

    async def async_get_bill(self, platform_card_no: str) -> dict[str, Any]:
        return await self._post(
            BILL_URL,
            {
                "appKey": generate_app_key(),
                "debug": "true",
                "platformOnlyCardNo": str(platform_card_no),
                "token": self._token,
            },
        )

    async def async_get_data(self) -> dict[str, Any]:
        card = await self.async_get_bind_card()
        cardbind_id = str(card.get("cardbindID") or card.get("cardbindId") or "")
        platform_card_no = str(card.get("platformCardNo") or "")

        if not cardbind_id or not platform_card_no:
            raise EcejGasApiError("户号信息不完整")

        balance = _first((await self.async_get_balance(cardbind_id)).get("data"))
        bill = _first((await self.async_get_bill(platform_card_no)).get("data"))
        ladder = _first(bill.get("ordinaryMeterLadderList"))

        return {
            "balance": _to_number(balance.get("billingData")),
            "meter_reading": _to_number(bill.get("thisMeterReading")),
            "last_meter_reading": _to_number(bill.get("lastTimeMeterReading")),
            "latest_bill_gas": _to_number(bill.get("thisGasConsumption")),
            "latest_bill_amount": _to_number(bill.get("totalAmount") or ladder.get("total")),
            "gas_price": _to_number(ladder.get("gasPrice")),
            "ladder": ladder.get("jTName"),
            "query_date": bill.get("sortDate") or bill.get("statementDate"),
            "meter_reading_date": bill.get("meterReadingDate"),
            "bill_status": bill.get("status"),
            "meter_type": _meter_type(card),
            "last_update_time": dt_util.now().strftime("%Y-%m-%d %H:%M:%S"),
            "cardbind_id": cardbind_id,
            "platform_only_card_no": platform_card_no,
            "pay_no": card.get("payNo"),
            "family_account_name": card.get("familyAccounName") or card.get("familyAccountName"),
            "user_name": card.get("userName"),
            "address": card.get("address"),
            "company_name": card.get("companyName"),
            "company_code": card.get("companyCode"),
            "business_type": card.get("businessType"),
            "business_name": card.get("businessName"),
            "city_id": card.get("cityId") or self._city_id,
        }
