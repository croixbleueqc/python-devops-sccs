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

from typing import Any, Callable
from weakref import WeakValueDictionary

from anyio.streams.memory import MemoryObjectSendStream

from .watcher import Watcher


class Scheduler(object):
    """
    Wrap API values in a WatcherType object.
    Exists solely to support the legacy API.
    """

    def __init__(self):
        self._watchers = WeakValueDictionary()

    async def watch(
            self,
            identity: tuple,
            poll_interval: int,
            send_stream: MemoryObjectSendStream,
            func,
            args: tuple = (),
            kwargs: dict = None,
            event_filter: Callable[[Any], bool] = lambda _: True,
            ):
        if kwargs is None:
            kwargs = {}

        wid = hash(identity)
        try:
            w = self._watchers[wid]
        except KeyError:
            w = Watcher(wid, poll_interval, func, args, kwargs, event_filter)
            self._watchers[wid] = w

        try:
            await w.start(send_stream)
        except Exception:
            del self._watchers[wid]
            raise

    async def notify(self, identity: tuple):
        """
        Notify watcher to update is content due to an outside event
        """
        wid = hash(identity)
        try:
            w = self._watchers[wid]
            w.refresh(True)
        except KeyError:
            pass
