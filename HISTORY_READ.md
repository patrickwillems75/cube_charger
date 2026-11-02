# Read finished transactions without modifying history

`cube_charger.read_history` is a response-only action. Provide an explicit
`config_entry_id`, configured `charge_box_id`, and `start`/`end` timestamps with
timezone offsets. It uses the existing Cube API client; no second credential is
required. It returns individual completed sessions including unknown or `AUTO`
identities, without changing the existing per-car totals or history store.

Each call is limited to 31 days, ten pages of 100 rows and 45 seconds. Invalid
pages, conflicting duplicate records, missing meters and exhausted pagination
raise a safe error rather than returning partial success or zero sessions.
RFID values and credentials are omitted. Energy is the meter delta using the
existing client's Wh assumption; verify that unit against an independent portal
export before allocating energy. Timestamps without an offset remain unresolved.
A complete API page sequence does not prove a complete calendar-day measurement,
especially for sessions crossing midnight.

Offline tests (Python 3.11+): `python tests/test_history_read.py`.

Installation is an integration update and requires restarting Home Assistant to
load the new module and action. Back up first. Existing charging controls and
automatic per-car aggregation are unchanged. These tests use mock clients, not
the HA runtime or a live upstream connection.
