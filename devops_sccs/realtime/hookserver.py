import multiprocessing
import uvicorn
from multiprocessing import Manager
from multiprocessing.managers import SyncManager
import threading
import logging
import asyncio
from ..cache import AsyncCache
from fastapi import FastAPI
import time
# sccs fast api server entrypoint
app_sccs = FastAPI()

cust_logger = logging.getLogger("aiohttp.access") 

class HookServer:
    """
    Class that run a uvicorn server with an async cache manager.
    """
    def __init__(self, settings):
        self.host = settings['host']
        self.port = settings['port']
        self.lifespan = 'on'
        self.manager = Manager()
        self.loop = asyncio.new_event_loop()
        

    def start_server(self):
        #print([{"path": route.path, "name": route.name} for route in app_sccs.routes])
        def fn(loop):
            asyncio.set_event_loop(loop)
            try:
                uvicorn.run(app_sccs, host = self.host, port = self.port, access_log = True, lifespan = self.lifespan)
            except RuntimeError as s:
                cust_logger.error("hook server shut down")
        
        self.threadedServer = threading.Thread(target = fn, args = (self.loop, ))  
        self.threadedServer.start()

    def stop_server(self):
        self.lifespan = 'off'
        self.loop.stop()
        self.loop.close()
        self.threadedServer.join(timeout=0)
    
    def create_cache(self , lookup_func = None,key_arg = None , **kwargs_func):
        return AsyncCache(self.manager.dict(),lookup_func,key_arg,self.manager.RLock(),**kwargs_func)
