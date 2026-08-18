
from __future__ import annotations
import aiohttp
from typing import Any, Optional

class CubeApi:
    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        verify_ssl: bool = True,
        timeout: int = 60,
        session: aiohttp.ClientSession | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._token = bearer_token
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._session = session

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Authorization": f"Bearer {self._token}"}

    def _session_timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=self.timeout)

    def _request_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "headers": self._headers(),
            "timeout": self._session_timeout(),
        }
        if self._session is None:
            kwargs["ssl"] = self.verify_ssl
        return kwargs

    def _get_session(self) -> tuple[aiohttp.ClientSession, bool]:
        if self._session is not None:
            return self._session, False
        return aiohttp.ClientSession(), True

    async def _json_request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        session, should_close = self._get_session()
        try:
            async with session.request(
                method,
                url,
                params=params,
                json=payload,
                **self._request_kwargs(),
            ) as response:
                response.raise_for_status()
                return await response.json()
        finally:
            if should_close:
                await session.close()

    async def list_chargeboxes(self) -> list[dict[str, Any]]:
        url = f"{self.base_url}/api/v1/CubeCharging/chargebox/details"
        data = await self._json_request("GET", url)
        return data if isinstance(data, list) else []

    async def chargebox_status(self, chargebox_id: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/api/v1/CubeCharging/chargebox/status/{chargebox_id}"
        data = await self._json_request("GET", url)
        return data if isinstance(data, list) else []

    async def set_max_total_current(self, chargebox_id: str, max_current: int) -> dict[str, Any]:
        if not 10 <= int(max_current) <= 32:
            raise ValueError("max_current must be between 10 and 32 amperes")
        url = f"{self.base_url}/api/v1/CubeCharging/chargebox/set-max-total-current"
        data = await self._json_request(
            "POST",
            url,
            payload={"chargeBoxId": chargebox_id, "maxCurrent": int(max_current)},
        )
        return data if isinstance(data, dict) else {}

    async def remote_start(self, chargebox_id: str, connector_id: int, idtag: str) -> dict[str, Any]:
        url = f"{self.base_url}/api/v1/CubeCharging/chargebox/remote-start"
        payload = {"chargeBoxId": chargebox_id, "connectorId": connector_id, "idTag": idtag}
        return await self._json_request("POST", url, payload=payload)

    async def remote_stop(self, chargebox_id: str, *, transaction_id: int | None = None, connector_id: int | None = None) -> dict[str, Any]:
        """Stop an active session. Requires chargeBoxId + (transactionId or connectorId)."""
        if transaction_id is None and connector_id is None:
            raise ValueError("remote_stop requires transaction_id or connector_id")
        url = f"{self.base_url}/api/v1/CubeCharging/chargebox/remote-stop"
        payload: dict[str, Any] = {"chargeBoxId": chargebox_id}
        if transaction_id is not None:
            payload["transactionId"] = int(transaction_id)
        else:
            payload["connectorId"] = int(connector_id)  # type: ignore[arg-type]
        return await self._json_request("POST", url, payload=payload)

    async def active_transactions(self, chargebox_id: str, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        url = f"{self.base_url}/api/v1/CubeCharging/chargebox/transactions/active"
        params = {"chargeBoxId": chargebox_id, "offset": offset, "limit": limit}
        data = await self._json_request("GET", url, params=params)
        return data if isinstance(data, list) else []

    async def history_transactions(
        self,
        start_iso: Optional[str] = None,
        end_iso: Optional[str] = None,
        chargebox_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 100
    ) -> list[dict[str, Any]]:
        url = f"{self.base_url}/api/v1/CubeCharging/chargebox/transactions/history"
        params = {"offset": offset, "limit": limit}
        if start_iso: params["startDate"] = start_iso
        if end_iso: params["endDate"] = end_iso
        if chargebox_id: params["chargeBoxId"] = chargebox_id
        data = await self._json_request("GET", url, params=params)
        return data if isinstance(data, list) else []

    async def reset_chargebox(self, chargebox_id: str, reset_type: str) -> dict[str, Any]:
        """Reset a charge box. reset_type should be 'Hard' or 'Soft'."""
        if reset_type not in ["Hard", "Soft"]:
            raise ValueError("reset_type must be 'Hard' or 'Soft'")
        url = f"{self.base_url}/api/v1/CubeCharging/chargebox/reset"
        payload = {"chargeBoxId": chargebox_id, "resetType": reset_type}
        return await self._json_request("POST", url, payload=payload)
