"""Offline safety/ledger-contract tests; no Home Assistant install or live token."""
import asyncio
import ast
from copy import deepcopy
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("history_read", ROOT / "custom_components/cube_charger/history_read.py")
history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(history)


def row(number=1, **changes):
    result = dict(transactionPk=number, transactionId=number, chargeBoxId="TEST-POINT", connectorId=1,
                  startTimestamp="2026-10-30T21:24:59", stopTimestamp="2026-10-31T08:43:14",
                  startValue="1000", stopValue="9776", idTag="AUTO")
    result.update(changes)
    return result


class FakeApi:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    async def read_history_page(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        value = self.pages[min(len(self.calls)-1, len(self.pages)-1)]
        if isinstance(value, BaseException):
            raise value
        return value


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request = dict(config_entry_id="entry", charge_box_id="TEST-POINT",
                            start="2026-10-01T00:00:00+00:00", end="2026-11-01T00:00:00+00:00")

    async def run_read(self, pages, request=None, mapping=None):
        api = FakeApi(pages)
        store = dict(totals={"Existing": 42}, processed_pks=[987], last_stop_ts="old")
        entries = {"entry": dict(api=api, coord=SimpleNamespace(data={"TEST-POINT": {}}),
                                 idtag_map=mapping or {}, store_data=store)}
        before = deepcopy(store)
        result = await history.read_history(entries, self.request if request is None else request)
        self.assertEqual(store, before)
        return result, api

    async def test_auto_kept_and_start_meter_subtracted(self):
        result, _ = await self.run_read([[row()]])
        self.assertEqual(result["sessions"][0]["energy_kWh"], "8.776")
        self.assertEqual(result["sessions"][0]["identity_kind"], "AUTO")
        self.assertIsNone(result["sessions"][0]["vehicle_label"])
        self.assertEqual(result["naive_timestamps"], 1)
        self.assertFalse(result["daily_allocation_proven"])

    async def test_zero_sessions_explicit(self):
        result, _ = await self.run_read([[]])
        self.assertEqual(result["session_count"], 0)
        self.assertTrue(result["pagination_complete"])

    async def test_mapped_label_without_returning_rfid(self):
        result, _ = await self.run_read([[row(idTag="PRIVATE-CARD-SENTINEL")]], mapping={"PRIVATE-CARD-SENTINEL": "Audi"})
        self.assertEqual(result["sessions"][0]["vehicle_label"], "Audi")
        self.assertNotIn("PRIVATE-CARD-SENTINEL", json.dumps(result))

    async def test_pagination_and_duplicate_page_boundary(self):
        page = [row(i) for i in range(100)]
        result, api = await self.run_read([page, [row(99), row(100)]])
        self.assertEqual(result["session_count"], 101)
        self.assertEqual(result["duplicate_rows"], 1)
        self.assertEqual(api.calls[1][1]["offset"], 100)

    async def test_repeated_full_page_rejected(self):
        with self.assertRaisesRegex(history.HistoryReadError, "PAGINATION_NOT_ADVANCING"):
            await self.run_read([[row(i) for i in range(100)]])

    async def test_page_limit_no_partial_success(self):
        with self.assertRaisesRegex(history.HistoryReadError, "HISTORY_PAGE_LIMIT"):
            await self.run_read([[row(p*100+i) for i in range(100)] for p in range(10)])

    async def test_conflicting_duplicates(self):
        with self.assertRaisesRegex(history.HistoryReadError, "CONFLICTING_DUPLICATE"):
            await self.run_read([[row(), row(stopValue="9999")]])

    async def test_malformed_page_not_zero(self):
        for value in ({"error": "secret"}, None):
            with self.subTest(value=value), self.assertRaises(history.HistoryReadError):
                await self.run_read([value])

    async def test_upstream_errors_never_leak_payload(self):
        try:
            await self.run_read([RuntimeError("PRIVATE-TOKEN-SENTINEL")])
        except history.HistoryReadError as error:
            self.assertEqual(str(error), "UPSTREAM_HISTORY_READ_FAILED")
            self.assertNotIn("SENTINEL", str(error))
        else:
            self.fail("Upstream error accepted")

    async def test_timeout_is_distinct(self):
        with self.assertRaisesRegex(history.HistoryReadError, "HISTORY_TIMEOUT"):
            await self.run_read([TimeoutError()])

    async def test_window_and_target_must_be_explicit(self):
        bad = [dict(self.request, start="2026-10-01"), dict(self.request, end="2026-11-02T00:00:00Z"),
               dict(self.request, end=self.request["start"]), dict(self.request, config_entry_id="other"),
               dict(self.request, charge_box_id="other"), dict(self.request, url="https://other.invalid")]
        for key in ("config_entry_id", "charge_box_id"):
            missing=dict(self.request);del missing[key];bad.append(missing)
        for request in bad:
            with self.subTest(request=request), self.assertRaises(history.HistoryReadError):
                await self.run_read([[]], request=request)

    async def test_missing_bad_and_reset_meters_rejected(self):
        for change in ({"startValue": None}, {"stopValue": "NaN"}, {"stopValue": "Infinity"},
                       {"stopValue": "-1"}, {"stopValue": "999"}, {"startValue": True}):
            with self.subTest(change=change), self.assertRaises(history.HistoryReadError):
                await self.run_read([[row(**change)]])

    async def test_identity_and_closed_interval_required(self):
        for change in ({"transactionPk": None}, {"connectorId": None}, {"chargeBoxId": "OTHER"},
                       {"stopTimestamp": None}, {"stopTimestamp": "2025-01-01T00:00:00"},
                       {"startTimestamp": "2026-10-30T21:24:59Z"}):
            with self.subTest(change=change), self.assertRaises(history.HistoryReadError):
                await self.run_read([[row(**change)]])

    async def test_explicit_offset_preserved(self):
        result, _ = await self.run_read([[row(startTimestamp="2026-10-30T21:24:59+01:00", stopTimestamp="2026-10-31T08:43:14+01:00")]])
        self.assertEqual(result["naive_timestamps"], 0)

    async def test_backend_ignoring_window_is_rejected(self):
        with self.assertRaisesRegex(history.HistoryReadError,"SESSION_OUTSIDE_WINDOW"):
            await self.run_read([[row(startTimestamp="2021-01-01T00:00:00",stopTimestamp="2021-01-02T00:00:00")]])

    def test_service_is_registered_as_response_only_in_async_setup(self):
        source=(ROOT / "custom_components/cube_charger/__init__.py").read_text(encoding="utf-8-sig")
        tree=ast.parse(source)
        setup=next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name=="async_setup")
        registrations=[n for n in ast.walk(setup) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="async_register"]
        self.assertEqual(len(registrations),1)
        keyword=next(k.value for k in registrations[0].keywords if k.arg=="supports_response")
        self.assertEqual(ast.unparse(keyword),"SupportsResponse.ONLY")

    async def test_strict_api_page_does_not_turn_error_objects_into_zero(self):
        spec=importlib.util.spec_from_file_location("cube_api_test", ROOT / "custom_components/cube_charger/api.py")
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{"aiohttp":SimpleNamespace()}):
            spec.loader.exec_module(module)
        api=module.CubeApi("https://example.invalid", "NEVER-SENT-SENTINEL")
        calls=[]
        async def fake(method,url,**kwargs):
            calls.append((method,url,kwargs))
            return {"error":"NEVER-SENT-SENTINEL"}
        api._json_request=fake
        with self.assertRaisesRegex(ValueError,"INVALID_HISTORY_PAGE"):
            await api.read_history_page("start","end","TEST-POINT",0,100)
        self.assertEqual(calls[0][0],"GET")
        self.assertTrue(calls[0][1].endswith("/transactions/history"))
        self.assertEqual(calls[0][2]["params"]["chargeBoxId"],"TEST-POINT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
