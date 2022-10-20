# Copyright 2021 Croix Bleue du Québec

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
from typing import Any, Callable

from .watcher import Watcher


class Scheduler(object):
    """
    Scheduler module

    Managing Watchers, subscription, unsubscribtion...
    """

    def __init__(self):
        self.tasks = {}
        self._lock = asyncio.Lock()

    async def watch(
            self,
            identity: tuple,
            poll_interval: int,
            func,
            filtering: Callable[[Any], bool] = lambda _: True,
            *args,
            **kwargs,
            ):
        """
        Run a new task or connect to an existing task
        """
        wid = hash(identity)
        logging.debug(f"wid: {hex(wid)}")
        w: Watcher | None = None

        # Protect task creation
        async with self._lock:
            w = self.tasks.get(wid)

        if w is None:
            logging.debug(f"scheduler: creating a new watcher for {hex(wid)}")
            w = Watcher(wid, poll_interval, func, *args, **kwargs)
            async with self._lock:
                self.tasks[wid] = w

        queue: asyncio.Queue = asyncio.Queue()
        try:
            # Create client, connect to the watcher and read events

            await w.subscribe(queue)

            while True:
                event = await queue.get()
                queue.task_done()

                if isinstance(event, Watcher.CloseSessionOnException):
                    raise event.get_exception()

                if filtering(event):
                    yield event
        except asyncio.CancelledError:
            logging.debug("cancelled")
        finally:
            # disconnect from the watcher
            try:
                await w.unsubscribe(queue)
            except asyncio.CancelledError:
                pass

            # Protect task update
            async with self._lock:
                if w.is_empty():
                    logging.debug(f"scheduler: remove watcher {hex(wid)}")
                    self.tasks.pop(wid)

    def notify(self, identity: tuple):
        """
        Notify watcher to update is content due to an outside event
        """
        wid = hash(identity)

        w = self.tasks.get(wid)

        if w is not None:
            logging.debug(f"notify {hex(wid)}")
            w.refresh(True)
