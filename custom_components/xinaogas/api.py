from __future__ import annotations

import hashlib
import re
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.util import dt as dt_util

from .const import (
    APPKEY_SALT,
    BILL_LIST_URL,
    BILL_SIGN_SALT,
    BILL_URL,
    BIND_CARDS_URL,
    ENERGY_ANALYSIS_URL,
    ENERGY_ANALYSIS_URL2,
    METER_GAS_URL,
    TOKEN_APPLY_URL,
    USER_AGENT,
)


class XinaoGasApiError(Exception):
    pass


class XinaoGasAuthError(XinaoGasApiError):
    pass


def generate_app_key() -> str:
    text = dt_util.now().strftime("%Y%m%d%H%M%S")
    return text + hashlib.md5((text + APPKEY_SALT).encode()).hexdigest()


def generate_bill_sign(payment_no: str) -> str:
    text = dt_util.now().strftime("%Y%m%d%H%M%S")
    return text + hashlib.md5((text + payment_no + BILL_SIGN_SALT).encode()).hexdigest()


def to_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list) and value:
        return value[0] if isinstance(value[0], dict) else {}
    return value if isinstance(value, dict) else {}


def find_list(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in keys:
            items = value.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    return []


def card_key(card: dict[str, Any]) -> str:
    return str(card.get("platformCardNo") or card.get("platformOnlyCardNo") or card.get("contractNo") or card.get("paymentNo") or "")


def payment_no(card: dict[str, Any]) -> str:
    return str(card.get("paymentNo") or card.get("payNo") or "")


def contract_no(card: dict[str, Any]) -> str:
    return str(card.get("contractNo") or card.get("contractNO") or card.get("paymentNo") or "")


def platform_card_no(card: dict[str, Any]) -> str:
    return str(card.get("platformCardNo") or card.get("platformOnlyCardNo") or "")


def is_gas_card(card: dict[str, Any]) -> bool:
    business_type = str(card.get("businessType") or "")
    text = " ".join(str(card.get(key) or "") for key in ("cardType", "businessName", "typeName"))
    return business_type in {"4", "20", "21", "200"} or "燃气" in text or "物联" in text


def meter_type(card: dict[str, Any], meter: dict[str, Any]) -> str | None:
    return (
        card.get("cardType")
        or card.get("businessName")
        or meter.get("meterTypeDesc")
        or meter.get("meterType")
        or None
    )


class XinaoGasApi:
    def __init__(self, session: ClientSession, token: str) -> None:
        self._session = session
        self.token = token.strip()

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
            "Referer": "https://servicewechat.com/wxd722317df8c566fe/0/page-frame.html",
        }

    def _check(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise XinaoGasApiError("接口返回格式异常")
        code = str(payload.get("resultCode"))
        message = str(payload.get("message") or payload.get("msg") or "接口返回失败")
        if code == "200":
            return payload
        if code in {"-20101", "401", "403", "1001", "1002", "1003"} or "token" in message.lower() or "登录" in message:
            raise XinaoGasAuthError(message)
        raise XinaoGasApiError(f"{message}（{code}）")

    async def _request(self, method: str, url: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            if method == "get":
                async with self._session.get(url, params=data, headers=self._headers(), timeout=ClientTimeout(total=25)) as resp:
                    resp.raise_for_status()
                    return self._check(await resp.json(content_type=None))
            async with self._session.post(url, data=data, headers=self._headers(), timeout=ClientTimeout(total=25)) as resp:
                resp.raise_for_status()
                return self._check(await resp.json(content_type=None))
        except ClientError as err:
            raise XinaoGasApiError(str(err)) from err
        except ValueError as err:
            raise XinaoGasApiError("接口返回无法解析") from err

    async def async_apply_token(self) -> dict[str, Any]:
        try:
            async with self._session.get(
                TOKEN_APPLY_URL,
                params={"env": "2", "token": self.token},
                headers=self._headers(),
                timeout=ClientTimeout(total=25),
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
        except ClientError as err:
            raise XinaoGasApiError(str(err)) from err
        except ValueError as err:
            raise XinaoGasApiError("接口返回无法解析") from err

        if not isinstance(payload, dict):
            raise XinaoGasApiError("接口返回格式异常")

        code = str(payload.get("resultCode"))
        data = first_dict(payload.get("data"))
        if code == "200":
            new_token = data.get("newToken") or data.get("token")
            if new_token:
                self.token = str(new_token)
            return data
        if code == "301":
            return data

        message = str(payload.get("message") or payload.get("msg") or "token续期失败")
        if code in {"-20101", "401", "403", "1001", "1002", "1003"} or "token" in message.lower() or "登录" in message:
            raise XinaoGasAuthError(message)
        raise XinaoGasApiError(f"{message}（{code}）")

    async def async_get_bind_cards(self) -> list[dict[str, Any]]:
        payload = await self._request(
            "post",
            BIND_CARDS_URL,
            {
                "token": self.token,
                "appKey": generate_app_key(),
                "clientType": "gaswx",
                "moduleCode": "1",
            },
        )
        cards = [dict(item) for item in find_list(payload.get("data")) if is_gas_card(item)]
        if not cards:
            raise XinaoGasApiError("未找到绑定的燃气户号")
        cards.sort(key=lambda item: 0 if str(item.get("businessType") or "") in {"4", "21", "200"} else 1)
        return cards

    async def async_get_card(self, platform_no: str = "", contract: str = "", payment: str = "") -> dict[str, Any]:
        cards = await self.async_get_bind_cards()
        for card in cards:
            if platform_no and platform_card_no(card) == platform_no:
                return card
            if contract and contract_no(card) == contract:
                return card
            if payment and payment_no(card) == payment:
                return card
        return cards[0]

    async def async_get_bill(self, card: dict[str, Any]) -> dict[str, Any]:
        pno = payment_no(card)
        return first_dict(
            (
                await self._request(
                    "get",
                    BILL_URL,
                    {
                        "token": self.token,
                        "clientType": "gaswx",
                        "appKey": generate_app_key(),
                        "companyCode": card.get("companyCode") or "",
                        "paymentNo": pno,
                        "sign": generate_bill_sign(pno),
                    },
                )
            ).get("data")
        )

    async def async_get_bill_list(self, card: dict[str, Any]) -> dict[str, Any]:
        payload = await self._request(
            "get",
            BILL_LIST_URL,
            {
                "token": self.token,
                "clientType": "gaswx",
                "appKey": generate_app_key(),
                "platformOnlyCardNo": platform_card_no(card),
            },
        )
        return first_dict(payload.get("data"))

    async def async_get_energy_analysis(self, card: dict[str, Any]) -> dict[str, Any]:
        data = {
            "token": self.token,
            "clientType": "gaswx",
            "appKey": generate_app_key(),
            "paymentNo": payment_no(card),
            "companyCode": card.get("companyCode") or "",
        }
        try:
            payload = await self._request("post", ENERGY_ANALYSIS_URL, data)
        except XinaoGasApiError:
            payload = await self._request("post", ENERGY_ANALYSIS_URL2, data)
        return first_dict(payload.get("data"))

    async def async_get_meter_gas_info(self, card: dict[str, Any]) -> dict[str, Any]:
        return first_dict(
            (
                await self._request(
                    "post",
                    METER_GAS_URL,
                    {
                        "refreshFlag": "false",
                        "appKey": generate_app_key(),
                        "clientType": "gaswx",
                        "token": self.token,
                        "contractNo": contract_no(card),
                    },
                )
            ).get("data")
        )
