
from __future__ import annotations
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from . import DOMAIN
from .api import CubeApi


def _first_box(coordinator):
    data = coordinator.data or {}
    return next(iter(data.items()), (None, {}))


class CubeCoordinatorSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry_id: str, key: str, name: str):
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{key}"


class CubeConnectionStatusSensor(CubeCoordinatorSensor):
    _attr_icon = "mdi:ev-station"

    @property
    def native_value(self):
        _, box = _first_box(self.coordinator)
        connectors = box.get("_connectors", [])
        if not box:
            return "Niet beschikbaar"
        if connectors:
            return "Online"
        return "Wachten op live status"

    @property
    def extra_state_attributes(self):
        chargebox_id, box = _first_box(self.coordinator)
        return {
            "charge_box_id": chargebox_id,
            "description": box.get("description"),
            "firmware": box.get("fwVersion"),
            "last_heartbeat": box.get("lastHeartbeatTimestamp"),
            "model": box.get("chargePointModel"),
        }


class CubeConnectorStatusSensor(CubeCoordinatorSensor):
    _attr_icon = "mdi:ev-plug-type2"

    def __init__(self, coordinator, entry_id: str, connector_id: int):
        super().__init__(coordinator, entry_id, f"connector_{connector_id}_status", f"Cube connector {connector_id} status")
        self._connector_id = connector_id

    def _connector(self):
        _, box = _first_box(self.coordinator)
        return next(
            (item for item in box.get("_connectors", []) if item.get("connectorId") == self._connector_id),
            {},
        )

    @property
    def native_value(self):
        return self._connector().get("status", "Onbekend")

    @property
    def extra_state_attributes(self):
        connector = self._connector()
        return {
            "status_timestamp": connector.get("statusTimestamp"),
            "error_code": connector.get("errorCode"),
            "error_info": connector.get("errorInfo"),
        }


class CubeCurrentEnergySensor(CubeCoordinatorSensor):
    _attr_device_class = "energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:lightning-bolt"

    @property
    def native_value(self):
        _, box = _first_box(self.coordinator)
        values = []
        for connector in box.get("_connectors", []):
            try:
                values.append(float(connector.get("currentEnergy") or 0) / 1000.0)
            except (TypeError, ValueError):
                continue
        return round(sum(values), 3)


class CubeSystemCurrentSensor(CubeCoordinatorSensor):
    _attr_native_unit_of_measurement = "A"
    _attr_icon = "mdi:current-ac"

    @property
    def native_value(self):
        _, box = _first_box(self.coordinator)
        return box.get("maximumSystemCurrent")

class CubeCarTotalEnergySensor(SensorEntity, RestoreEntity):
    _attr_device_class = "energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = "total_increasing"

    def __init__(self, hass: HomeAssistant, entry_id: str, car_name: str):
        self.hass = hass
        self.entry_id = entry_id
        self.car_name = car_name
        self._attr_name = f"Cube {car_name} energie totaal"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_total_{car_name}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                self._attr_native_value = float(last.state)
            except:  # noqa
                self._attr_native_value = 0.0
        self.hass.bus.async_listen(f"{DOMAIN}_history_updated", self._on_history_updated)

    @callback
    def _on_history_updated(self, _):
        data = self.hass.data[DOMAIN][self.entry_id]["store_data"]
        total = data["totals"].get(self.car_name, 0.0)
        self._attr_native_value = round(total, 3)
        self.async_write_ha_state()

class CubeCarActiveEnergySensor(CoordinatorEntity, SensorEntity):
    _attr_device_class = "energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, hass: HomeAssistant, entry_id: str, api: CubeApi, car_name: str, unit_active: str):
        super().__init__(hass.data[DOMAIN][entry_id]["coord"])
        self.hass = hass
        self.entry_id = entry_id
        self.api = api
        self.car_name = car_name
        self.unit_active = unit_active
        self._attr_name = f"Cube {car_name} actieve sessie"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_active_{car_name}"

    @property
    def native_value(self):
        data = self.hass.data[DOMAIN][self.entry_id]
        idmap = data["idtag_map"]
        coord = data["coord"]

        value_kwh = 0.0
        cids = list((coord.data or {}).keys())
        chargebox_id = cids[0] if cids else None
        if not chargebox_id:
            return None

        txs = (coord.data or {}).get(chargebox_id, {}).get("_active_transactions", [])
        for t in txs:
            idtag = t.get("idTag")
            if idmap.get(idtag) != self.car_name:
                continue
            cur = t.get("currentEnergy")
            try:
                v = float(cur)
                if self.unit_active == "Wh":
                    v = v / 1000.0
                value_kwh += v
            except (TypeError, ValueError):
                continue

        return round(value_kwh, 3)

class CubeWhoIsChargingSensor(CoordinatorEntity, SensorEntity):
    """Text sensor showing which idTag/auto is currently charging."""
    _attr_icon = "mdi:account"

    def __init__(self, hass: HomeAssistant, entry_id: str, api: CubeApi):
        super().__init__(hass.data[DOMAIN][entry_id]["coord"])
        self.hass = hass
        self.entry_id = entry_id
        self.api = api
        self._attr_name = "Cube wie laadt nu"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_who_is_charging"
        self._attr_extra_state_attributes = {}

    @property
    def native_value(self):
        data = self.hass.data[DOMAIN][self.entry_id]
        idmap = data["idtag_map"]
        coord = data["coord"]
        cids = list((coord.data or {}).keys())
        chargebox_id = cids[0] if cids else None
        if not chargebox_id:
            self._attr_extra_state_attributes = {}
            return "Geen"
        txs = (coord.data or {}).get(chargebox_id, {}).get("_active_transactions", [])
        # Pak de eerste relevante transactie (of combineer meerdere)
        active = []
        for t in txs:
            idtag = t.get("idTag")
            car = idmap.get(idtag, "Onbekende laadpas")
            active.append({
                "car": car,
                "transactionPk": t.get("transactionPk"),
                "connectorId": t.get("connectorId"),
                "currentEnergy_kWh": float(t.get("currentEnergy") or 0.0)
            })
        if not active:
            self._attr_extra_state_attributes = {}
            return "Geen"
        else:
            # Indien meerdere, toon de eerste in state en allemaal in attributes
            self._attr_extra_state_attributes = {
                "active": active
            }
            return active[0]["car"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    api: CubeApi = data["api"]
    idmap = data["idtag_map"]

    entities = []
    coordinator = data["coord"]
    connector_id = data["connector_id"]

    entities.extend([
        CubeConnectionStatusSensor(coordinator, entry.entry_id, "connection_status", "Cube laadpaal status"),
        CubeConnectorStatusSensor(coordinator, entry.entry_id, connector_id),
        CubeCurrentEnergySensor(coordinator, entry.entry_id, "current_energy", "Cube actieve sessie energie"),
        CubeSystemCurrentSensor(coordinator, entry.entry_id, "maximum_system_current", "Cube maximale systeemstroom"),
    ])

    # cumulatief + actief per auto
    for car in sorted(set(idmap.values())):
        entities.append(CubeCarTotalEnergySensor(hass, entry.entry_id, car))
        entities.append(CubeCarActiveEnergySensor(hass, entry.entry_id, api, car, data["energy_unit_active"]))

    # wie-laadt-nu sensor (1 tekstsensor)
    entities.append(CubeWhoIsChargingSensor(hass, entry.entry_id, api))

    async_add_entities(entities)
