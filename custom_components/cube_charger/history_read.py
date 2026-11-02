"""Bounded, side-effect-free history access through the already configured API."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

PAGE_SIZE = 100
MAX_PAGES = 10
MAX_DAYS = 31
TIMEOUT_SECONDS = 45


class HistoryReadError(ValueError):
    """A safe error code, without upstream payloads or authentication details."""


def _timestamp(value: Any, *, aware: bool = False) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise HistoryReadError("INVALID_TIMESTAMP")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise HistoryReadError("INVALID_TIMESTAMP") from None
    if aware and result.tzinfo is None:
        raise HistoryReadError("WINDOW_REQUIRES_OFFSET")
    return result


def _identifier(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise HistoryReadError("MISSING_SESSION_IDENTITY")
    text = str(value)
    if not text or len(text) > 128 or "|" in text or any(ord(c) < 32 for c in text):
        raise HistoryReadError("INVALID_SESSION_IDENTITY")
    return text


def _meter(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None or len(str(value)) > 80:
        raise HistoryReadError("MISSING_METER_VALUE")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise HistoryReadError("INVALID_METER_VALUE") from None
    if not number.is_finite() or number < 0 or number > Decimal("1e15"):
        raise HistoryReadError("INVALID_METER_VALUE")
    return number


def _session(row: Any, box_id: str, idmap: dict) -> dict:
    if not isinstance(row, dict):
        raise HistoryReadError("INVALID_SESSION")
    identity = _identifier(row.get("transactionPk"))
    if row.get("chargeBoxId") is not None and str(row["chargeBoxId"]) != box_id:
        raise HistoryReadError("WRONG_CHARGE_POINT")
    connector = _identifier(row.get("connectorId"))
    if not connector.isdigit() or not 1 <= int(connector) <= 10:
        raise HistoryReadError("INVALID_CONNECTOR")
    raw_start, raw_stop = row.get("startTimestamp"), row.get("stopTimestamp")
    start, stop = _timestamp(raw_start), _timestamp(raw_stop)
    if (start.tzinfo is None) != (stop.tzinfo is None) or stop < start:
        raise HistoryReadError("INVALID_SESSION_INTERVAL")
    before, after = _meter(row.get("startValue")), _meter(row.get("stopValue"))
    if after < before:
        raise HistoryReadError("SESSION_METER_RESET")
    # Wh is the existing Cube integration's assumption; reconcile with an export
    # before permitting automatic energy allocation from this value.
    energy = (after - before) / Decimal(1000)
    tag = row.get("idTag")
    label = idmap.get(tag) if isinstance(tag, str) else None
    if not isinstance(label, str) or len(label) > 80:
        label = None
    transaction_id = row.get("transactionId")
    return {
        "key": f"{box_id}|{connector}|{identity}",
        "source_record_id": identity,
        "transaction_id": _identifier(transaction_id) if transaction_id is not None else None,
        "charge_box_id": box_id,
        "connector_id": connector,
        "start": raw_start,
        "stop": raw_stop,
        "timestamps_have_offset": start.tzinfo is not None,
        "start_meter_Wh": str(before),
        "stop_meter_Wh": str(after),
        "energy_kWh": str(energy),
        "vehicle_label": label,
        "identity_kind": "AUTO" if tag == "AUTO" else "mapped_tag" if label else "unmapped_tag",
    }


async def read_history(entries: dict, request: dict) -> dict:
    """Read one exact point and <=31 days; never change store, totals or devices."""
    if set(request) - {"config_entry_id", "charge_box_id", "start", "end"}:
        raise HistoryReadError("UNSUPPORTED_ARGUMENT")
    start, end = _timestamp(request.get("start"), aware=True), _timestamp(request.get("end"), aware=True)
    if not timedelta(0) < end - start <= timedelta(days=MAX_DAYS):
        raise HistoryReadError("WINDOW_OUT_OF_BOUNDS")
    entry_id = request.get("config_entry_id")
    if not isinstance(entry_id, str):
        raise HistoryReadError("CONFIG_ENTRY_REQUIRED")
    if entry_id not in entries:
        raise HistoryReadError("CONFIG_ENTRY_NOT_FOUND")
    data = entries[entry_id]
    boxes = data["coord"].data or {}
    box_id = request.get("charge_box_id")
    if box_id is None:
        raise HistoryReadError("CHARGE_POINT_REQUIRED")
    box_id = _identifier(box_id)
    if box_id not in boxes:
        raise HistoryReadError("CHARGE_POINT_NOT_CONFIGURED")
    sessions: dict[str, dict] = {}
    page_fingerprints = set()
    duplicate_rows = 0
    terminal = False
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            for page in range(MAX_PAGES):
                rows = await data["api"].read_history_page(
                    start.isoformat(), end.isoformat(), chargebox_id=box_id,
                    offset=page * PAGE_SIZE, limit=PAGE_SIZE,
                )
                if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
                    raise HistoryReadError("INVALID_HISTORY_PAGE")
                page_keys = []
                for row in rows:
                    item = _session(row, box_id, data.get("idtag_map", {}))
                    session_start, session_stop = _timestamp(item["start"]), _timestamp(item["stop"])
                    if session_start.tzinfo is not None:
                        if session_stop < start or session_start > end:
                            raise HistoryReadError("SESSION_OUTSIDE_WINDOW")
                    else:
                        # Offset is unknown: reject clearly unrelated dates while
                        # preserving the ambiguity rather than inventing a timezone.
                        lower = start.replace(tzinfo=None) - timedelta(days=1)
                        upper = end.replace(tzinfo=None) + timedelta(days=1)
                        if session_stop < lower or session_start > upper:
                            raise HistoryReadError("SESSION_OUTSIDE_WINDOW")
                    key = item["key"]
                    page_keys.append(key)
                    if key in sessions:
                        if sessions[key] != item:
                            raise HistoryReadError("CONFLICTING_DUPLICATE")
                        duplicate_rows += 1
                    else:
                        sessions[key] = item
                fingerprint = tuple(page_keys)
                if len(rows) == PAGE_SIZE and fingerprint in page_fingerprints:
                    raise HistoryReadError("PAGINATION_NOT_ADVANCING")
                page_fingerprints.add(fingerprint)
                if len(rows) < PAGE_SIZE:
                    terminal = True
                    break
    except HistoryReadError:
        raise
    except TimeoutError:
        raise HistoryReadError("HISTORY_TIMEOUT") from None
    except Exception:
        raise HistoryReadError("UPSTREAM_HISTORY_READ_FAILED") from None
    if not terminal:
        raise HistoryReadError("HISTORY_PAGE_LIMIT")
    return {
        "schema": 1,
        "source": "cube_charger.read_history",
        "config_entry_id": entry_id,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "charge_box_id": box_id,
        "pagination_complete": True,
        "duplicate_rows": duplicate_rows,
        "session_count": len(sessions),
        "sessions": sorted(sessions.values(), key=lambda s: s["key"]),
        "energy_unit_basis": "Wh per existing Cube client; export reconciliation required",
        "naive_timestamps": sum(not s["timestamps_have_offset"] for s in sessions.values()),
        "daily_allocation_proven": False,
        "device_commands": 0,
        "store_writes": 0,
    }
