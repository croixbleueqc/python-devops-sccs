            
import asyncio
import multiprocessing
import threading
import uvicorn
import signal
from hypercorn.asyncio import serve
from hypercorn.config import Config
import time

from httpx import AsyncClient
from fastapi import FastAPI
from devops_sccs.cache import *


app_test = FastAPI()

#analog to hook server
class CacheTest_Server:
    manager = multiprocessing.Manager()
    def __init__(self) :
        pass   

    @staticmethod
    def __run_test_async(func, *args,**kwargs):

        def run_func(*args,**kwargs):
            return asyncio.run(func(*args,**kwargs))
            
        p= multiprocessing.Process(target = run_func,args=args , kwargs= kwargs)
        p.start()
        p.join()  
    
    @classmethod
    def run_test_post_from_thread(cls,path):
        async def fn(app_):
            async with AsyncClient(app=app_,base_url="http://devops-console") as client:
               return await client.post(path)
        cls.__run_test_async(fn,app_test)
          
    @classmethod
    def run_test_edit_cache_in_dict(cls,cache,cache_key,key,value):
        
        async def fn(cache,cache_key,key,value):
            cache[cache_key][key]=value
        cls.__run_test_async( fn,cache,cache_key,key,value)
    
    @classmethod
    def run_test_edit_cache_alone(cls,cache:AsyncCache,key,value):
        async def fn(cache,key,value):
            cache[key]=value
        cls.__run_test_async( fn,cache,key,value)
    
    @classmethod
    def run_test_edit_from_class(cls,obj,cache_key,key,value):
        async def fn(obj,cache_key,key,value):
            obj.cache[cache_key][key]=value
        cls.__run_test_async( fn,obj,cache_key,key,value)
    
    @classmethod
    def __run_server(cls,fn,path,*args,**kwargs):
        try:
            threadedServer = multiprocessing.Process(target = fn, args = args,kwargs= kwargs,daemon=True)
            threadedServer.start()
            time.sleep(1)
            async def async_post():
                async with AsyncClient() as client:
                    return await client.post(path)
            cls.__run_test_async(async_post)

        except Exception as s :
            raise s
        finally:
            threadedServer.terminate()
            
    @classmethod
    def run_test_run_uvicorn(cls,path):
        cls.__run_server(uvicorn.run,f"http://localhost:5001{path}",app_test, host = 'localhost', port = 5001, access_log = True)


    @classmethod        
    def run_test_run_hypercorn(cls,path):
        
        def run_func(func,*args,**kwargs):
            return asyncio.run(func(*args,**kwargs))
        
        config = Config()
        config.bind = ["localhost:5001"]

        cls.__run_server(run_func,f"http://localhost:5001{path}",serve,app_test,config)

    def create_cache(self , lookup_func = None,key_arg = None , **kwargs_func):
        return AsyncCache(self.manager.dict(),lookup_func,key_arg,self.manager.RLock(),**kwargs_func)