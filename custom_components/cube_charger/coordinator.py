
from __future__ import annotations
import asyncio
import logging
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.core import HomeAssistant
from .api import CubeApi

_LOGGER = logging.getLogger(__name__)

class CubeCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, api: CubeApi, poll: int):
        super().__init__(hass, _LOGGER, name="Cube Charger", update_interval=timedelta(seconds=poll))
        self.api = api
        self._boxes: dict[str, dict] = {}
        self._refresh_count = 0

    async def _async_update_data(self):
        self._refresh_count += 1

        # Cube's public API can take tens of seconds to answer. Fetch the
        # relatively static details on the first refresh and then periodically,
        # while connector/session state is refreshed every cycle.
        if not self._boxes or self._refresh_count % 20 == 0:
            boxes = await self.api.list_chargeboxes()
            self._boxes = {
                box["chargeBoxId"]: {
                    **box,
                    "_connectors": self._boxes.get(box["chargeBoxId"], {}).get("_connectors", []),
                    "_active_transactions": self._boxes.get(box["chargeBoxId"], {}).get("_active_transactions", []),
                }
                for box in boxes
                if box.get("chargeBoxId")
            }

        # Do not delay initial config-entry setup with another slow API call.
        # The normal coordinator timer will populate live status afterwards.
        if self._refresh_count == 1:
            return self._boxes

        ids = list(self._boxes)
        results = await asyncio.gather(
            *(self.api.chargebox_status(chargebox_id) for chargebox_id in ids),
            *(self.api.active_transactions(chargebox_id) for chargebox_id in ids),
            return_exceptions=True,
        )
        for index, chargebox_id in enumerate(ids):
            status_result = results[index]
            active_result = results[index + len(ids)]
            if not isinstance(status_result, Exception):
                self._boxes[chargebox_id]["_connectors"] = status_result
            else:
                _LOGGER.warning("Status update failed for %s: %s", chargebox_id, status_result)
            if not isinstance(active_result, Exception):
                self._boxes[chargebox_id]["_active_transactions"] = active_result
            else:
                _LOGGER.warning("Active-session update failed for %s: %s", chargebox_id, active_result)

        return self._boxes
