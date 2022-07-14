# Copyright 2022 Croix Bleue du Québec

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

import logging
from collections import UserDict
import multiprocessing
from threading import Lock
from typing import Any


class AsyncCache(object):
    def __init__(
        self,
        data,
        lookup_func,
        key_arg: str = None,
        rlock=multiprocessing.RLock(),
        **kwargs_func,
    ):
        """
        loopup_func:async   callable  function to call when a key is not found in the cache
        key_arg:string      name of the key argument
        rlock:Rlock         recursive lock for writing (needs a context manager)
        data:dict           initial data of the cache
        kwargs_func         arguments to call with lookup_func
        """

        self.data = data
        # setup lookup function
        self.lookup_func = lookup_func
        self.key_arg = key_arg
        self.kwargs_func = kwargs_func
        self.rlock = rlock
        print(f"new cache at : {self.data.__repr__()}")

    def get(self, key):
        return self.data.get(key)

    def clear_cache(self):
        self.data.clear()

    async def __getitem__(self, key):
        val = self.data.get(key)
        if val is None:

            if self.lookup_func is not None:
                with self.rlock as lock:
                    # check if the data is still unitialized
                    val = self.data.get(key)
                    if val is None:
                        # data is definetly unitialized
                        print(
                            f"element {key} not found in the cache at {self.data.__repr__()}! populating it!"
                        )
                        self.kwargs_func[self.key_arg] = key
                        val = await self.lookup_func(**self.kwargs_func)
                        self.data[key] = val

            else:

                raise KeyError(key)

        return val

    def __setitem__(self, key, item):
        with self.rlock as lock:
            print(f"key {key} has been set at {self.data.__repr__()} !")
            self.data[key] = item

    def __enter__(self):
        self.rlock.acquire()
        return self.data

    def __exit__(self, type, value, traceback):
        self.rlock.release()

    def __new__(cls, *args, **kwargs):
        logging.debug("new cache!")
        return super().__new__(cls)

    def __getstate__(self):
        logging.debug("get state cache")
        return self.__dict__

    def __setstate__(self, state):
        logging.debug("set state cache")
        self.__dict__ = state

    def __copy__(self):
        logging.debug("copy cache!")
        self.__dict__.copy()
