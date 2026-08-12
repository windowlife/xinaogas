from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import XinaoGasApi, XinaoGasApiError
from .const import DOMAIN

TOKEN_STORE_KEY = f"{DOMAIN}_token"

TOKEN_RENEW_AHEAD_MS = 7200000
TOKEN_CLOSE_RENEW_AHEAD_MS = 3600000
TOKEN_RECHECK_INTERVAL_MS = 1800000
TOKEN_RECHECK_CLOSE_INTERVAL_MS = 600000
TOKEN_MIN_SCHEDULE_DELAY_MS = 60000


class XinaoGasTokenManager:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = Store(hass, 1, TOKEN_STORE_KEY)
        self.data: dict[str, Any] = {}
        self._loaded = False
        self._unsub: Callable[[], None] | None = None

    async def async_load(self) -> None:
        if self._loaded:
            return
        self.data = await self.store.async_load() or {}
        self._loaded = True

    async def async_save(self) -> None:
        await self.store.async_save(self.data)

    async def async_get_token(self) -> str:
        await self.async_load()
        return str(self.data.get("token") or "").strip()

    async def async_set_token(self, token: str, token_ttl: int | None = None) -> None:
        await self.async_load()
        token = token.strip()
        if not token:
            return
        now = self._now_ms()
        self.data["token"] = token
        if token_ttl:
            self.data["token_ttl"] = int(token_ttl)
            self.data["token_save_time"] = now
            self.data["token_next_check"] = self._next_token_check(now, int(token_ttl))
        elif not self.data.get("token_next_check"):
            self.data["token_next_check"] = now + TOKEN_MIN_SCHEDULE_DELAY_MS
        await self.async_save()
        self.async_schedule()

    async def async_import_entry_token(self, token: str, token_ttl: int | None = None) -> None:
        await self.async_load()
        if self.data.get("token"):
            return
        await self.async_set_token(token, token_ttl)

    async def async_get_api(self) -> XinaoGasApi:
        token = await self.async_get_token()
        return XinaoGasApi(async_get_clientsession(self.hass), token)

    async def async_probe_token(self) -> None:
        await self.async_load()
        await self._renew_token(force=True)
        self.async_schedule()

    async def async_ensure_valid_token(self) -> None:
        await self.async_load()
        now = self._now_ms()
        next_check = int(self.data.get("token_next_check") or 0)
        if not next_check or now >= next_check:
            await self._renew_token(force=False)
            self.async_schedule()

    def async_schedule(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

        next_check = int(self.data.get("token_next_check") or 0)
        if not next_check:
            return

        now = self._now_ms()
        check_at = max(next_check, now + TOKEN_MIN_SCHEDULE_DELAY_MS)
        when = datetime.fromtimestamp(check_at / 1000, timezone.utc)
        self._unsub = async_track_point_in_utc_time(self.hass, self._async_token_check, when)

    def async_cancel(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def _async_token_check(self, _now: datetime) -> None:
        self._unsub = None
        await self._renew_token(force=False)
        self.async_schedule()

    async def _renew_token(self, force: bool = False) -> None:
        await self.async_load()
        token = str(self.data.get("token") or "").strip()
        if not token:
            return

        now = self._now_ms()
        ttl = int(self.data.get("token_ttl") or 0)
        save_time = int(self.data.get("token_save_time") or 0)
        if ttl and save_time:
            remain = save_time + ttl - now
            if remain > TOKEN_RENEW_AHEAD_MS and not force:
                self.data.update(
                    {
                        "token_ttl": remain,
                        "token_save_time": now,
                        "token_next_check": self._next_token_check(now, remain),
                    }
                )
                await self.async_save()
                return

        api = XinaoGasApi(async_get_clientsession(self.hass), token)
        try:
            data = await api.async_apply_token()
        except XinaoGasApiError:
            self.data.update(
                {
                    "token_last_check": now,
                    "token_next_check": now + TOKEN_RECHECK_CLOSE_INTERVAL_MS,
                }
            )
            await self.async_save()
            return

        ttl = int(data.get("newTokenTtl") or data.get("tokenTtl") or data.get("currentTokenTtl") or ttl or 0)
        self.data.update(
            {
                "token": api.token,
                "token_ttl": ttl,
                "token_save_time": now,
                "token_last_check": now,
                "token_next_check": self._next_token_check(now, ttl),
            }
        )
        await self.async_save()

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

    def _now_ms(self) -> int:
        return int(dt_util.utcnow().timestamp() * 1000)
