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

from asyncio import Lock,Condition
from contextlib import asynccontextmanager
import logging

class RW_Lock (object):
    def __init__(self):
       self._read_ready = Condition(Lock())
       self._readers = 0

    @asynccontextmanager
    async def read(self):
        async with self._read_ready:
            self._readers +=1
        try :        
            yield
        finally:
            async with self._read_ready:
                self._readers -= 1
                if self._readers == 0:
                    self._read_ready.notify_all()

    @asynccontextmanager
    async def write(self):
        async with self._read_ready as WLock:
            await self._read_ready.wait_for(lambda : self._readers == 0)
            yield
        
class AsyncCache(object):
    
    def __init__ (self ,lookup_func=None,key_arg=None, data:dict = {} , **kwargs_func):
        """
        loopup_func:async   callable  function to call when a key is not found in the cache
        key_arg:string      name of the key argument 
        data:dict           initial data of the cache
        kwargs_func         arguments to call with lookup_func
        """

        self.lockCache = RW_Lock()
        self.data = data

        #setup lookup function
        self.lookup_func = lookup_func
        self.key_arg = key_arg
        self.kwargs =  kwargs_func

    def get(self,key):
        return self.data.get(key)

    async def __getitem__(self, key):
        val = None
        try :
            #async with self.lockCache.read() as RLock:
                val = self.data[key]
                logging.debug(u"retrived {val} in the cache at location {key}")
        except KeyError as e:
            logging.debug(u"element {key} not found in the cache! populating it!")
            if self.lookup_func is None:
                raise e
            else:
               # async with self.lockCache.write() as WLock:
                    self.kwargs[self.key_arg] = key
                    val = await self.lookup_func(**self.kwargs)
                    self.data[key]=val
                    
        return val

    def __setitem__(self, key, item) :
            logging.debug(u"assign {item} to {key}")
            self.data[key]=item
        
    def __del__(self):
        if(self.current_task is not None):
            self.current_task.result()
