"""
Scheduler module

Managing Watchers, subscription, unsubscribtion...
"""

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
from .watcher import Watcher

class Scheduler(object):
    def __init__(self):
        self.tasks={}
        self.lock_tasks = asyncio.Lock()

    async def watch(self, identity: tuple, poll_interval: int, func, filtering=lambda event: True, **kwargs):
        """
        Run a new task or connect to an existing task
        """
        wid = hash(identity)
        logging.debug(f"wid: {wid}")

        # Protect task creation
        async with self.lock_tasks:
            w = self.tasks.get(wid)
            if w is None:
                logging.debug(f"scheduler: creating a new watcher for {wid}")
                w = Watcher(wid, poll_interval, func, **kwargs)
                self.tasks[wid] = w

        try:
            # Create client, connect to the watcher and read events

            client = asyncio.Queue()

            await w.subscribe(client)

            while True:
                event = await client.get()
                client.task_done()

                if isinstance(event, Watcher.CloseClientOnException):
                    raise event.get_exception()

                if filtering(event):
                    yield event
        except asyncio.CancelledError:
            logging.debug("cancelled")
        finally:
            # disconnect from the watcher
            try:
                await w.unsubscribe(client)
            except asyncio.CancelledError:
                pass

            # Protect task update
            async with self.lock_tasks:
                if w.is_no_watcher():
                    logging.debug(f"scheduler: remove watcher {wid}")
                    self.tasks.pop(wid)

    def notify(self, identity: tuple):
        """
        Notify watcher to update is content due to an outside event
        """
        wid = hash(identity)

        w = self.tasks.get(wid)

        if w is not None:
            logging.debug(f"notify {wid}")
            w.refresh()
