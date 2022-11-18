"""
Watcher module

Provide a way to poll an API and to stream results as events (ADD, MODIFY, DELETE)
"""

# Copyright 2021-2022 Croix Bleue du Québec

# This file is part of python-devops-sccs.

# python-devops-sccs is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# python-devops-sccs is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.

# You should have received a copy of the GNU Lesser General Public License
# along with python-devops-sccs.  If not, see <https://www.gnu.org/licenses/>.

from typing import Callable

import anyio
from loguru import logger

from ..redis import RedisCache
from ..typing import WatcherType
from ..typing.event import Event, EventType

_sentinel = object()

cache = RedisCache()


def standardize_watcher_values(values):
    if not isinstance(values, list):
        values = [values]
    values = list(
        map(
            lambda v: v if isinstance(v, WatcherType) else WatcherType(
                key=hash(str(v)),
                data=v.dict() if hasattr(v, "dict") else v,
                )
            , values
            )
        )
    return values


async def get_keys_to_delete(values, cache_values):
    values_keys = set(v.key for v in values)
    cache_keys = set(v.key for v in cache_values)
    delete_keys = cache_keys - values_keys
    return delete_keys


class Watcher(object):
    class CloseSessionOnException(object):
        def __init__(self, exception):
            self.exception = exception

        def get_exception(self):
            return self.exception

    def __init__(
            self,
            watcher_id: int,
            poll_interval: int,
            func: Callable,
            args: tuple,
            kwargs: dict,
            event_filter: Callable[..., bool] = lambda _: True,
            ):
        self.bypass_func_cache = False

        # function
        self.func = lambda: func(*args, fetch=self.bypass_func_cache, **kwargs)

        self.key = f"watcher:{watcher_id}"

        self.poll_interval = poll_interval
        self.poll_event = anyio.Event()

        self.event_filter = event_filter

        self.refresh()

    async def start(self, send_stream):
        if cache.exists(self.key):
            events = self.get_watcher_cache_values_as_events()
            if len(events) > 0:
                for event in events:
                    if self.event_filter(event):
                        await send_stream.send(event)
        else:
            async with anyio.create_task_group() as tg:
                tg.start_soon(self.fetch_events, send_stream)
                tg.start_soon(self.timed_refresh)

    async def fetch_events(self, send_stream):
        async for event in self.watch():
            if self.event_filter(event):
                await send_stream.send(event)

    async def stop(self):
        pass

    def get_watcher_cache_values_as_events(self) -> list[Event]:
        values: list[WatcherType] = cache.get(self.key)
        return list(
            map(lambda value: Event(_type=EventType.ADDED, value=value, key=value.key), values)
            )

    async def watch(self):
        """
        Watching for a specific resource
        """
        while True:
            await self.poll_event.wait()
            self.poll_event = anyio.Event()
            logger.debug(f"Watcher {self.key} is polling")

            try:
                values = await self.func()
                self.bypass_func_cache = False
            except Exception:
                raise

            values = standardize_watcher_values(values)

            events = []
            # Remove old values (those in cache but not in the new list)
            # !!! Important to retain the ordering of elements in the list because it's the only source
            # of truth for environment ordering on the frontend (e.g. master -> dev -> qa -> prod) in
            # the case of get_continuous_deployment_config calls)...
            cached_values: list[WatcherType] = cache.get(self.key, [])
            keys_to_delete = await get_keys_to_delete(values, cached_values)
            for key in keys_to_delete:
                value = next((v for v in cached_values if v.key == key), None)
                event = Event(
                    _type=EventType.DELETED,
                    value=value,
                    key=key
                    )
                events.append(event)

            # Add/update new values
            for value in values:
                cache_value = next((v for v in cached_values if v.key == value.key), _sentinel)
                if cache_value is _sentinel:  # new value
                    _type = EventType.ADDED
                elif cache_value != value:  # modified value
                    _type = EventType.MODIFIED
                else:  # no change
                    continue

                event = Event(key=value.key, _type=_type, value=value)

                events.append(event)

            # Update the cache
            cache.set(self.key, values)

            for event in events:
                yield event

    def refresh(self, fetch: bool = False):
        """
        Force a refresh (notify the watch to refresh as soon as possible); and optionally bypass the
        function's cache.
        """
        self.bypass_func_cache = fetch
        self.poll_event.set()

    async def timed_refresh(self):
        while True:
            self.refresh()
            await anyio.sleep(self.poll_interval)
