            
import asyncio
import multiprocessing
import threading
import uvicorn
import signal
from hypercorn.config import Config
from hypercorn.asyncio import serve


from httpx import AsyncClient
from fastapi import FastAPI,Request
from devops_sccs.cache import *


app_test = FastAPI()

#analog to hook server
class CacheTest_Server(object):
    def __init__(self) :
        self.manager = multiprocessing.Manager()

    @staticmethod
    def __run_test_async(func, *args,**kwargs):

        def run_func(*args,**kwargs):
            return asyncio.run(func(*args,**kwargs))
            
        p= multiprocessing.Process(target = run_func,args=args , kwargs= kwargs)
        p.start()
        p.join()  
    
    @staticmethod
    def run_test_post_from_thread():
        async def fn(app_):
            async with AsyncClient(app=app_,base_url="http://devops-console") as client:
               return await client.post("/a")
        CacheTest_Server.__run_test_async(fn,app_test)
          
    @staticmethod
    def run_test_edit_cache_in_dict(cache,cache_key,key,value):
        
        async def fn(cache,cache_key,key,value):
            cache[cache_key][key]=value
        CacheTest_Server.__run_test_async( fn,cache,cache_key,key,value)
    
    @staticmethod
    def run_test_edit_cache_alone(cache:AsyncCache,key,value):
        async def fn(cache,key,value):
            cache[key]=value
        CacheTest_Server.__run_test_async( fn,cache,key,value)
    
    @staticmethod
    def run_test_edit_from_class(obj,cache_key,key,value):
        async def fn(obj,cache_key,key,value):
            obj.cache[cache_key][key]=value
        CacheTest_Server.__run_test_async( fn,obj,cache_key,key,value)

    def __run_server(self,fn,stop_fn,path,**kwargs):
        try:
            self.loop = asyncio.new_event_loop()
            self.threadedServer = threading.Thread(target = fn, args = (self.loop, ),kwargs= kwargs)
            self.threadedServer.daemon=True  
            self.threadedServer.start()
            async def async_post():
                async with AsyncClient() as client:
                    return await client.post(path)
            self.__run_test_async(async_post)
            stop_fn()
        except Exception as s :
            raise s
        finally:
            self.loop.close()
            self.threadedServer.join(timeout=0)
            

    def run_test_run_uvicorn(self):
        self.lifespan= self.manager.Value(str,'on')
        def fn(loop):
            asyncio.set_event_loop(loop)
            try:
                uvicorn.run(app_test, host = 'localhost', port = 5002, access_log = True,)
            except RuntimeError as s:
                pass

        def stop_fn():
            self.lifespan ='off'

        self.__run_server(fn,stop_fn,"http://localhost:5002/a")

        
        

    def create_cache(self , lookup_func = None,key_arg = None , **kwargs_func):
        return AsyncCache(self.manager.dict(),lookup_func,key_arg,self.manager.RLock(),**kwargs_func)