from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import XinaoGasCoordinator


@dataclass(frozen=True, kw_only=True)
class XinaoGasSensorDescription(SensorEntityDescription):
    pass


SENSORS: tuple[XinaoGasSensorDescription, ...] = (
    XinaoGasSensorDescription(key="current_month_amount", translation_key="current_month_amount", native_unit_of_measurement="元", icon="mdi:cash", state_class=SensorStateClass.MEASUREMENT),
    XinaoGasSensorDescription(key="current_month_usage", translation_key="current_month_usage", native_unit_of_measurement=UnitOfVolume.CUBIC_METERS, icon="mdi:fire", state_class=SensorStateClass.MEASUREMENT),
    XinaoGasSensorDescription(key="meter_reading_date", translation_key="meter_reading_date", icon="mdi:calendar-check"),
    XinaoGasSensorDescription(key="latest_bill_date", translation_key="latest_bill_date", icon="mdi:calendar-search"),
    XinaoGasSensorDescription(key="gas_price", translation_key="gas_price", native_unit_of_measurement="元/m³", icon="mdi:cash-multiple", state_class=SensorStateClass.MEASUREMENT),
    XinaoGasSensorDescription(key="ladder", translation_key="ladder", icon="mdi:stairs"),
    XinaoGasSensorDescription(key="balance", translation_key="balance", native_unit_of_measurement="元", icon="mdi:currency-cny", state_class=SensorStateClass.MEASUREMENT),
    XinaoGasSensorDescription(key="meter_reading", translation_key="meter_reading", native_unit_of_measurement=UnitOfVolume.CUBIC_METERS, device_class=SensorDeviceClass.GAS, icon="mdi:counter", state_class=SensorStateClass.TOTAL_INCREASING),
    XinaoGasSensorDescription(key="last_meter_reading", translation_key="last_meter_reading", native_unit_of_measurement=UnitOfVolume.CUBIC_METERS, device_class=SensorDeviceClass.GAS, icon="mdi:counter", state_class=SensorStateClass.TOTAL),
    XinaoGasSensorDescription(key="last_update_time", translation_key="last_update_time", icon="mdi:update"),
    XinaoGasSensorDescription(key="bill_status", translation_key="bill_status", icon="mdi:receipt-text-check"),
    XinaoGasSensorDescription(key="month_total_gas", translation_key="month_total_gas", native_unit_of_measurement=UnitOfVolume.CUBIC_METERS, icon="mdi:fire-circle", state_class=SensorStateClass.MEASUREMENT),
    XinaoGasSensorDescription(key="daily_usage", translation_key="daily_usage", native_unit_of_measurement=UnitOfVolume.CUBIC_METERS, icon="mdi:calendar-today", state_class=SensorStateClass.MEASUREMENT),
    XinaoGasSensorDescription(key="cumulative_usage", translation_key="cumulative_usage", native_unit_of_measurement=UnitOfVolume.CUBIC_METERS, device_class=SensorDeviceClass.GAS, icon="mdi:meter-gas", state_class=SensorStateClass.TOTAL_INCREASING),
    XinaoGasSensorDescription(key="valve_status", translation_key="valve_status", icon="mdi:valve"),
    XinaoGasSensorDescription(key="battery_status", translation_key="battery_status", icon="mdi:battery"),
    XinaoGasSensorDescription(key="iot_update_time", translation_key="iot_update_time", icon="mdi:clock-outline"),
    XinaoGasSensorDescription(key="meter_type", translation_key="meter_type", icon="mdi:meter-gas"),
    XinaoGasSensorDescription(key="company_name", translation_key="company_name", icon="mdi:domain"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: XinaoGasCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(XinaoGasSensor(coordinator, entry, description) for description in SENSORS)


class XinaoGasSensor(CoordinatorEntity[XinaoGasCoordinator], SensorEntity):
    entity_description: XinaoGasSensorDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator: XinaoGasCoordinator, entry: ConfigEntry, description: XinaoGasSensorDescription) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        value = data.get(self.entity_description.key)
        if value not in (None, ""):
            return value
        if self.entity_description.key in {
            "company_name",
            "meter_type",
            "iot_update_time",
            "battery_status",
            "valve_status",
            "bill_status",
            "latest_bill_date",
            "meter_reading_date",
            "last_update_time",
            "ladder",
        }:
            return "未知"
        return None

    @property
    def device_info(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        payment_no = str(data.get("payment_no") or "").strip()
        device_id = str(data.get("platform_card_no") or data.get("contract_no") or self.entry.entry_id)
        return {
            "identifiers": {(DOMAIN, device_id)},
            "name": f"新奥燃气 {payment_no}" if payment_no else "新奥燃气",
            "manufacturer": "新奥燃气",
            "model": data.get("company_name") or data.get("card_type") or "微信小程序",
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        attrs = {
            "家庭名称": data.get("family_account_name"),
            "用户名称": data.get("user_name"),
            "缴费号": data.get("payment_no"),
            "合同号": data.get("contract_no"),
            "地址": data.get("address"),
            "燃气公司": data.get("company_name"),
            "cityId": data.get("city_id"),
            "businessType": data.get("business_type"),
            "cardType": data.get("card_type"),
            "platformCardNo": data.get("platform_card_no"),
        }
        if self.entity_description.key in {"daily_usage", "cumulative_usage"}:
            attrs["每日用气量"] = data.get("daily_usage_map")
        if self.entity_description.key == "daily_usage":
            attrs["日用气日期"] = data.get("daily_usage_date")
        if self.entity_description.key == "cumulative_usage":
            attrs["基准抄表时间"] = data.get("base_meter_reading_time")
            attrs["抄表后用气合计"] = data.get("after_reading_usage")
        return {key: value for key, value in attrs.items() if value not in (None, "", [], {})} or None
