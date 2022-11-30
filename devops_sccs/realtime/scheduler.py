# Copyright 2021 Croix Bleue du Québec
import logging
from typing import Any, Callable

from anyio import (
    create_memory_object_stream,
    create_task_group,
    Event, Lock, TASK_STATUS_IGNORED, get_cancelled_exc_class,
    )
from anyio.abc import TaskStatus
from anyio.streams.memory import MemoryObjectSendStream

from .watcher import Watcher


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


class Scheduler(object):
    """
    Wrap API values in a WatcherType object.
    Exists solely to support the legacy API.
    """

    def __init__(self):
        self.watchers = {}
        self.lock = Lock()

    async def watch(
            self,
            identity: tuple,
            poll_interval: int,
            send_stream: MemoryObjectSendStream,
            cancel_event: Event(),
            func,
            args: tuple = (),
            kwargs: dict = None,
            event_filter: Callable[[Any], bool] = lambda _: True,
            ):
        if kwargs is None:
            kwargs = {}

        wid = hash(identity)

        async with self.lock:
            w = self.watchers.get(wid)
            if w is None:
                w = Watcher(wid, poll_interval, func, args, kwargs)
                self.watchers[wid] = w

        async def process_watcher_events(
                receive_stream,
                task_status: TaskStatus = TASK_STATUS_IGNORED
                ):
            task_status.started()
            async with receive_stream:
                async for event in receive_stream:
                    if isinstance(event, Watcher.CloseClientOnException):
                        raise event.get_exception()
                    if event_filter(event):
                        await send_stream.send(event)

        async with create_task_group() as tg:
            try:
                w_send_stream, w_receive_stream = create_memory_object_stream()
                await tg.start(process_watcher_events, w_receive_stream)
                tg.start_soon(w.subscribe, w_send_stream)
                await cancel_event.wait()
                await w.unsubscribe(w_send_stream)
            except Exception:
                await w.unsubscribe(w_send_stream)
                raise
            except get_cancelled_exc_class():
                await w.stop()
                raise
            finally:
                if not w.has_subscribers():
                    async with self.lock:
                        del self.watchers[wid]

        logging.debug("Watcher cancelled")

        # logger.remove(log)

    async def notify(self, identity: tuple):
        """
        Notify watcher to update is content due to an outside event
        """
        wid = hash(identity)
        async with self.lock:
            try:
                w = self.watchers[wid]
                w.refresh(True)
            except KeyError:
                pass
