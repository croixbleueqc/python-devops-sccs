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
from ..typing.event import Event, EventType
from ..typing import WatcherTyping2
from ..errors import SccsException


class Watcher(object):
    _undef = object()

    class CloseClientOnException(object):
        def __init__(self, exception):
            self.exception = exception

        def get_exception(self):
            return self.exception

    def __init__(self, wid, poll_interval, func, *args, **kwargs):
        # Id
        self.wid = wid

        # Clients (subscribe/unsubscribe)
        self.clients = []
        self.lock_clients = asyncio.Lock()
        self.accept_clients = True

        # Polling
        self.poll_interval = poll_interval
        self.event_poll = asyncio.Event()

        # Caching
        self.cache = OrderedDict()

        # function
        self.func = func
        self.func_args = args
        self.func_kwargs = kwargs

        # tasks
        self.running_task = None

    def refresh(self):
        """
        Force a refresh (notify the watch to refresh as soon as possible)
        """
        # logging.info(f"watcher: refresh for {self.wid}")
        self.event_poll.set()

    async def subscribe(self, client: asyncio.Queue):
        """
        Subscribe a client
        """
        async with self.lock_clients:
            if not self.accept_clients:
                raise SccsException("watcher: can't accept new client !")

            self.clients.append(client)

            # First client ?
            if len(self.clients) == 1:
                self.start()
            elif len(self.cache) > 0:
                # Propagate previous events to the new client (all clients will be in sync after)
                for value in self.cache.values():
                    event = Event()
                    event.type_ = EventType.ADDED
                    event.value = value
                    event.key = value.key
                    client.put_nowait(event)

    async def unsubscribe(self, client: asyncio.Queue):
        """
        Unsubscribe a client
        """
        async with self.lock_clients:
            try:
                self.clients.remove(client)
            except ValueError:
                pass

            if len(self.clients) == 0:
                await self.stop()

    def is_no_watcher(self):
        """
        No client connected to this watcher
        """
        return len(self.clients) == 0

    async def watch(self):
        """
        Watching for a specific resource
        """

        while True:
            await self.event_poll.wait()
            self.event_poll.clear()

            values = await self.func(*self.func_args, **self.func_kwargs)

            if not isinstance(values, list):
                values = [values]

            # Protect the cache and list of clients
            async with self.lock_clients:

                # DELETED before
                values_keys = set((i.key for i in values))
                cache_keys = self.cache.keys()
                delete_keys = cache_keys - values_keys

                for key in delete_keys:
                    event = Event()
                    event.type_ = EventType.DELETED
                    event.value = self.cache.pop(key)
                    event.key = key

                    # Dispatch event
                    self._dispatch(event)

                # ADDED / MODIFIED
                for value in values:
                    if not isinstance(value, WatcherTyping2):
                        logging.error("watcher: value is invalid")
                        raise ValueError()

                    cache_value = self.cache.get(value.key, Watcher._undef)

                    if cache_value is Watcher._undef:
                        event = Event()
                        event.type_ = EventType.ADDED
                    elif cache_value != value:
                        event = Event()
                        event.type_ = EventType.MODIFIED
                    else:
                        # logging.info("identical !")
                        continue

                    event.value = value
                    event.key = value.key

                    # Update the cache
                    self.cache[event.key] = event.value

                    # Dispatch event
                    self._dispatch(event)

    def _dispatch(self, event):
        for client in self.clients:
            client.put_nowait(event)

    async def timed_refresh(self):
        """
        refresh at fixed interval (polling)
        """
        while True:
            self.refresh()
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
                async with self.lock_clients:
                    self.accept_clients = False
                    event = Watcher.CloseClientOnException(e)
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
            self.accept_clients = True
