from cachetest_server import *
#analog to SCCS in plugin.py
class CacheTest_PluginBase(object):
    def get_a(self,key,args=None):
        raise NotImplementedError

class func:
    def __init__(self,func):
        self.func = func
    async def run(self,a,b):
        return self.func(a,b)
#analog to bitbucket cloud
class CacheTest_PluginA(CacheTest_PluginBase):
    def __init__(self, test_server):
        self.path = "/a"
        self.b = 'b'
        self.c =  func( lambda a,b : a+b+'c')
        self.cache = {"a":test_server.create_cache(self.__fetch_a,"key")}
        self.init_api()

    async def get_a(self,key,args=None):
        return await self.cache["a"][key]
    
    async def __fetch_a(self,key):
        return await self.c.run(key,self.b)
    
    async def __set_a(self,key):
        t = await self.cache["a"]['a']
        t = await self.c.run('a',self.b)
        self.cache["a"]['a'] = t
    
    def init_api(self):
        @app_test.post(f"{self.path}")
        async def __handle_a(request:Request):
           await self.__set_a('a')
        return __handle_a