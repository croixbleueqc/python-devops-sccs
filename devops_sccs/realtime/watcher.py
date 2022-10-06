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

import asyncio
import logging
from collections import OrderedDict
from typing import Any, Callable

from ..errors import SccsException
from ..typing import WatcherType
from ..typing.event import Event, EventType


_sentinel = object()


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
        *args,
        bypass_func_cache=False,
        **kwargs
    ):
        # Id
        self.wid = watcher_id

        # Sessions (subscribe/unsubscribe)
        self.queues: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()
        self.accepts_queues = True

        # Polling
        self.poll_interval = poll_interval
        self.poll_event = asyncio.Event()
        self.bypass_func_cache = bypass_func_cache

        # Caching
        self.cache: OrderedDict[int, Any] = OrderedDict()

        # function
        self.func = lambda: func(*args, **kwargs, fetch=self.bypass_func_cache)

        # tasks
        self.running_task = None

    def refresh(self, bypass_cache: bool = False):
        """
        Force a refresh (notify the watch to refresh as soon as possible); and optionally bypass the
        function's cache (ats_cache)
        """
        self.bypass_func_cache = bypass_cache
        self.poll_event.set()

    async def subscribe(self, queue: asyncio.Queue):
        """
        Subscribe a session
        """
        async with self._lock:
            if not self.accepts_queues:
                raise SccsException("watcher: can't accept new session !")

            self.queues.append(queue)

            # First client ?
            if len(self.queues) == 1:
                self.start()
            elif len(self.cache) > 0:
                # Propagate previous events to the new client (all clients will be in sync after)
                for value in self.cache.values():
                    event = Event(_type=EventType.ADDED, value=value, key=value.key)
                    queue.put_nowait(event)

    async def unsubscribe(self, client: asyncio.Queue):
        """
        Unsubscribe a client
        """
        async with self._lock:
            try:
                self.queues.remove(client)
            except ValueError:
                pass

            if len(self.queues) == 0:
                await self.stop()

    def is_empty(self):
        """
        No client connected to this watcher
        """
        return len(self.queues) == 0

    async def watch(self):
        """
        Watching for a specific resource
        """

        while True:
            await self.poll_event.wait()
            self.poll_event.clear()

            values = await self.func()

            # !!! Revert cache bypass
            self.bypass_func_cache = False

            if not isinstance(values, list):
                values = [values]

            # Protect the cache and list of clients
            async with self._lock:
                for value in values:
                    if not isinstance(value, WatcherType):
                        logging.error("watcher: value is invalid")
                        value = WatcherType(
                            key=hash(str(value)),
                            data=value.dict() if hasattr(value, "dict") else value,
                        )

                # DELETED before
                values_keys = set(v.key for v in values)
                cache_keys = self.cache.keys()
                delete_keys = cache_keys - values_keys

                for key in delete_keys:
                    event = Event(
                        _type=EventType.DELETED,
                        value=self.cache.pop(key),
                        key=key,
                    )

                    # Dispatch event
                    self._dispatch(event)

                # ADDED / MODIFIED
                for value in values:
                    cache_value = self.cache.get(value.key, _sentinel)

                    _type: EventType

                    if cache_value is _sentinel:
                        _type = EventType.ADDED
                    elif cache_value != value:
                        _type = EventType.MODIFIED
                    else:
                        continue

                    event = Event(key=value.key, _type=_type, value=value)

                    # Update the cache
                    self.cache[event.key] = event.value

                    # Dispatch event
                    self._dispatch(event)

    def _dispatch(self, event):
        for queue in self.queues:
            queue.put_nowait(event)

    async def timed_refresh(self):
        """
        refresh at fixed interval (polling)
        """
        while True:
            self.refresh(self.bypass_func_cache)
            await asyncio.sleep(self.poll_interval)

    def start(self):
        """
        Start the watcher
        """
        if self.running_task is not None:
            logging.warning("watcher: already started !")
            return

        logging.info("starting watcher !")

        async def async_start():
            watch_task = None
            timed_task = None
            try:
                watch_task = asyncio.create_task(self.watch())
                timed_task = asyncio.create_task(self.timed_refresh())
                await asyncio.gather(watch_task, timed_task)
            except Exception as e:
                logging.error("watcher: an exception occured during the polling")
                if watch_task:
                    watch_task.cancel()
                if timed_task:
                    timed_task.cancel()

                # Notify all clients about the exception
                async with self._lock:
                    self.accepts_queues = False
                    event = Watcher.CloseSessionOnException(e)
                    self._dispatch(event)

                # stop will be called once all clients will unsubscribe to this watcher. This is handled by the scheduler
                # at this point, the watch is not running anymore and it is not possible to add new client on this watcher

        self.running_task = asyncio.create_task(async_start())

    async def stop(self):
        """
        Stop the watcher
        """
        if self.running_task is None:
            return

        logging.debug("watcher: stopping")

        self.running_task.cancel()

        try:
            await self.running_task
        finally:
            logging.debug("watcher: stopped")
            self.running_task = None
            self.accepts_queues = True
