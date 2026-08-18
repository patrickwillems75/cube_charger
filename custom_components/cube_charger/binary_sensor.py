from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN


class CubeChargingBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_name = "Cube laadt nu"

    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_charging"

    @property
    def is_on(self) -> bool:
        for box in (self.coordinator.data or {}).values():
            if box.get("_active_transactions"):
                return True
            if any(
                str(connector.get("status", "")).lower() == "charging"
                for connector in box.get("_connectors", [])
            ):
                return True
        return False


class CubeCarChargingBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.POWER

    def __init__(self, coordinator, entry_id: str, car_name: str, tags: set[str]):
        super().__init__(coordinator)
        self.car_name = car_name
        self.tags = tags
        self._attr_name = f"Cube {car_name} laadt nu"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_charging_{car_name}"

    @property
    def is_on(self) -> bool:
        for box in (self.coordinator.data or {}).values():
            if any(
                transaction.get("idTag") in self.tags
                for transaction in box.get("_active_transactions", [])
            ):
                return True
        return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coord"]
    idmap = data["idtag_map"]
    cars = sorted(set(idmap.values()))
    entities = [CubeChargingBinarySensor(coordinator, entry.entry_id)]
    entities.extend(
        CubeCarChargingBinarySensor(
            coordinator,
            entry.entry_id,
            car,
            {tag for tag, name in idmap.items() if name == car},
        )
        for car in cars
    )
    async_add_entities(entities)
