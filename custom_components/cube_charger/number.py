from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN


class CubeMaximumCurrentNumber(CoordinatorEntity, NumberEntity):
    """Controllable Cube IKL L-limit exposed by the official public API."""

    _attr_native_min_value = 10
    _attr_native_max_value = 32
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "A"
    _attr_mode = "slider"
    _attr_icon = "mdi:current-ac"

    def __init__(self, coordinator, api, entry_id: str, chargebox_id: str):
        super().__init__(coordinator)
        self.api = api
        self.chargebox_id = chargebox_id
        self._attr_name = "Cube maximale laadstroom"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{chargebox_id}_maximum_current"

    @property
    def native_value(self):
        box = (self.coordinator.data or {}).get(self.chargebox_id, {})
        value = box.get("maximumSystemCurrent")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        requested = int(value)
        response = await self.api.set_max_total_current(self.chargebox_id, requested)
        if not response.get("success") or response.get("status") != "Accepted":
            raise HomeAssistantError(
                response.get("message") or "Cube rejected the maximum charging current"
            )

        data = self.coordinator.data or {}
        box = data.get(self.chargebox_id)
        if box is not None:
            box["maximumSystemCurrent"] = int(response.get("maxCurrent", requested))
            self.coordinator.async_set_updated_data(data)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coord"]
    api = data["api"]
    async_add_entities(
        CubeMaximumCurrentNumber(coordinator, api, entry.entry_id, chargebox_id)
        for chargebox_id in (coordinator.data or {})
    )
