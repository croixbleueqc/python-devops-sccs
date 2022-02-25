import uvicorn
from multiprocessing import Manager
import threading
import logging
import asyncio
from ..cache import AsyncCache
from fastapi import FastAPI

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

    def start_server(self):
        logging.debug([{"path": route.path, "name": route.name} for route in app_sccs.routes])
        def fn(loop):
            asyncio.set_event_loop(loop)
            try:
                uvicorn.run(app_sccs, host = self.host, port = self.port, access_log = True, lifespan = self.lifespan)
            except RuntimeError as s:
                cust_logger.error("hook server shut down")

        loop = asyncio.new_event_loop()
        self.threadedServer = threading.Thread(target = fn, args = (loop, ))  
        self.threadedServer.start()

    async def stop_server(self):
        self.lifespan = 'off'
        self.threadedServer.join()        
    
    def create_cache(self , lookup_func = None,key_arg = None , **kwargs_func):
        return AsyncCache(lookup_func,key_arg,self.manager.dict(),**kwargs_func)