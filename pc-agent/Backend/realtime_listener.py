"""
Realtime command listener
=========================
Subscribes to Supabase Realtime postgres_changes on the `commands` table,
filtered by this device's id, and dispatches new pending rows to the
agent's existing _handle_command_sync callback.

Why a separate module
---------------------
supabase-py's *sync* client cannot open Realtime channels — only the async
client can. Rather than rewriting the whole agent async, we run the realtime
loop on its own thread with its own asyncio event loop, and bridge events
back via a thread-safe callback.

The 30s safety-net poll in agent._monitoring_loop still runs, so a missed
event during a reconnect will be picked up within ~30s.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("Realtime")

# Backoff bounds for reconnect. Doubles on each failure, capped.
_RECONNECT_MIN_S = 1.0
_RECONNECT_MAX_S = 30.0


class CommandListener:
    """Background thread running an asyncio loop with a Realtime channel.

    Usage:
        listener = CommandListener(
            url=SUPABASE_URL,
            anon_key=SUPABASE_ANON_KEY,
            access_token=session_jwt,
            device_id=device_id,
            on_command=agent._handle_command_sync,
        )
        listener.start()
        ...
        listener.stop()

    The callback is invoked from the listener thread. It must be safe to call
    without an asyncio loop on the current thread — _handle_command_sync is.
    """

    def __init__(
        self,
        url: str,
        anon_key: str,
        access_token: str,
        device_id: str,
        on_command: Callable[[dict], None],
    ):
        self._url = url
        self._anon_key = anon_key
        self._access_token = access_token
        self._device_id = device_id
        self._on_command = on_command

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_evt = threading.Event()
        self._connected = False
        self._last_event_ts = 0.0  # monotonic seconds; for liveness checks

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._thread_main, name="RealtimeListener", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_evt.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: None)  # nudge
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_event_age_s(self) -> float:
        if self._last_event_ts == 0.0:
            return float("inf")
        return time.monotonic() - self._last_event_ts

    # ------------------------------------------------------------------
    # Thread / loop
    # ------------------------------------------------------------------
    def _thread_main(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._run())
        except Exception:
            logger.exception("Realtime listener thread crashed")
        finally:
            try:
                if self._loop:
                    self._loop.close()
            except Exception:
                pass

    async def _run(self) -> None:
        backoff = _RECONNECT_MIN_S

        while not self._stop_evt.is_set():
            try:
                await self._open_channel()
                # Successful run: reset backoff.
                backoff = _RECONNECT_MIN_S
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._connected = False
                logger.warning(f"Realtime channel error: {e}")

            if self._stop_evt.is_set():
                return

            # Exponential backoff with cap.
            wait = min(backoff, _RECONNECT_MAX_S)
            logger.info(f"Reconnecting in {wait:.0f}s")
            await asyncio.sleep(wait)
            backoff = min(backoff * 2, _RECONNECT_MAX_S)

    async def _open_channel(self) -> None:
        """Open one Realtime connection and pump events until it dies."""
        # The `realtime` package is bundled with supabase-py.
        from realtime import AsyncRealtimeClient  # type: ignore

        socket_url = self._url.replace("https://", "wss://").rstrip("/") + "/realtime/v1"
        client = AsyncRealtimeClient(socket_url, self._anon_key)
        # Set the user's access token so RLS sees auth.uid() on the channel.
        try:
            await client.set_auth(self._access_token)
        except Exception:
            # Older realtime-py versions don't have set_auth; the JWT is
            # passed via query string when params include apikey + token.
            pass

        await client.connect()

        try:
            channel = client.channel(f"db-commands-{self._device_id}")

            def _on_insert(payload, *_args, **_kwargs) -> None:
                self._dispatch(payload)

            # API order in realtime>=2: (event, callback, table, schema, filter)
            channel.on_postgres_changes(
                "INSERT",
                _on_insert,
                table="commands",
                schema="public",
                filter=f"device_id=eq.{self._device_id}",
            )

            await channel.subscribe()
            self._connected = True
            logger.info(f"Realtime subscribed: db-commands-{self._device_id[:8]}…")

            # Block until the socket dies or we're asked to stop. is_connected
            # is a property, not a method — read it as a value.
            while not self._stop_evt.is_set():
                connected = getattr(client, "is_connected", True)
                if connected is False:
                    break
                await asyncio.sleep(1.0)
        finally:
            self._connected = False
            try:
                await client.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------
    def _dispatch(self, payload: dict) -> None:
        """Convert a Realtime payload into a command dict and invoke callback."""
        self._last_event_ts = time.monotonic()
        try:
            # Different realtime-py versions hand us the row in different keys.
            record = (
                payload.get("record")
                or payload.get("new")
                or (payload.get("data") or {}).get("record")
                or {}
            )
            if not isinstance(record, dict):
                return

            # Skip already-handled rows (poll fallback may have grabbed them).
            if record.get("status") and record.get("status") != "pending":
                return

            # Parse JSON payload string if needed.
            p = record.get("payload")
            if isinstance(p, str):
                try:
                    record["payload"] = json.loads(p)
                except (json.JSONDecodeError, TypeError):
                    record["payload"] = {}

            self._on_command(record)
        except Exception:
            logger.exception("Realtime dispatch failed")
