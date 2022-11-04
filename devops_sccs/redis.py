import functools
import os
import pickle
import weakref
from datetime import timedelta
from typing import Any

import dill
from loguru import logger
from redis.asyncio import Redis
from redis.asyncio.connection import BlockingConnectionPool  # will block and wait rather than raise an exception if a client tries to connect and the pool is full

from devops_sccs.plugins.cache_keys import CacheKeyFn


class Serializer:
    @staticmethod
    def needs_pickling(v):
        if isinstance(v, (str, int, float)):
            return False
        return True

    @staticmethod
    def needs_unpickling(v):
        if isinstance(v, bytes):
            return True
        return False

    @staticmethod
    def serialize(value: Any) -> bytes:
        serialized = value
        if Serializer.needs_pickling(value):
            try:
                serialized = pickle.dumps(value)
            except AttributeError:
                serialized = dill.dumps(value)  # slower than pickle, but can handle more types
        return serialized

    @staticmethod
    def deserialize(value) -> Any:
        deserialized = value
        if Serializer.needs_unpickling(value):
            try:
                deserialized = pickle.loads(value)
            except (pickle.UnpicklingError, AttributeError):
                deserialized = dill.loads(value)
        return deserialized


class RedisCache:
    """Basic singleton wrapper for redis client"""
    _cache = None

    def __new__(cls, *args, **kwargs):
        if not cls._cache:
            cls._cache = super().__new__(cls)
        return cls._cache

    def __init__(self):
        self.client = None
        self._is_initialized = False

    async def init(self):
        if self._is_initialized:
            return

        try:
            redis_password = os.environ['REDIS_PASSWORD']
            redis_host = os.environ.get('REDIS_HOST', 'localhost')

            redis_url = f'redis://:{redis_password}@{redis_host}:6379/0'
            self.client = Redis(
                connection_pool=BlockingConnectionPool.from_url(redis_url),
                decode_responses=True  # binary data otherwise
                )
            await self.client.initialize()
            await self.client.ping()  # test connection
        except Exception as e:
            logger.critical(e)
            self.client = None
            raise e

        self._is_initialized = True

    async def set(self, key, value, ttl=timedelta(hours=1)) -> bool:
        value = Serializer.serialize(value)
        return await self.client.set(key, value, ex=ttl)

    async def get(self, key) -> Any:
        value = await self.client.get(key)
        return Serializer.deserialize(value)

    async def exists(self, key) -> bool:
        return await self.client.exists(key)

    async def delete(self, *keys) -> int:
        return await self.client.delete(*keys)

    async def clear(self):
        await self.client.flushall()

    @property
    def initialized(self):
        return self._is_initialized

    @staticmethod
    def make_cache_key(func_name: str, args: tuple, kwargs: dict):
        return f'{func_name}({args}, {kwargs})'
        # key = args
        # if kwargs:
        #     for item in kwargs.items():
        #         key += item
        # try:
        #     hash_value = hash(key)
        # except TypeError:
        #     return str(key)  # for unhashable types (eg. dicts), just return the value of __str__()
        # return hash_value


def cache(
        ttl: timedelta,
        key: str | CacheKeyFn | None = None,
        prefix: str = "",
        ):
    """Wrapper for caching **method**  results in redis.

    Args:
        ttl: time to live for the cached value
        key: key to use for the cache. Can be an static string, a function or None. In the case of
        a function, it is expected to have some, or all of the same arguments as the wrapped method.
        prefix: prefix to use for the cache key. Useful for differentiating between different
        instances of the same class, for example.
    """

    def _decorator(method):
        async def _async_wrapper(_self, *args, fetch: bool, **kwargs):
            _cache = RedisCache()
            if not _cache.initialized:
                await _cache.init()

            # key
            _key = None
            if key is None:
                _key = RedisCache.make_cache_key(method.__name__, args, kwargs)
            elif isinstance(key, str):
                _key = key
            elif isinstance(key, CacheKeyFn):
                _key = key.infer_from_orig(method, *args, **kwargs)
            if _key is None:
                raise ValueError('Invalid key')

            if prefix:
                _key = f'{prefix}:{_key}'

            if fetch:
                logger.debug(f'REDIS CACHE: fetch flag set, deleting cached value')
                n = await _cache.delete(_key)
                logger.debug(f'REDIS CACHE: deleted {n} keys')

            cached = await _cache.get(_key)
            if cached is not None:
                logger.debug(f'REDIS CACHE HIT for key {_key}')
                return cached

            logger.debug(f'REDIS CACHE MISS for key {_key}')
            result = await method(_self(), *args, **kwargs)
            await _cache.set(_key, result, ttl=ttl)
            return result

        @functools.wraps(method)
        async def inner(self, *args, fetch=False, **kwargs):
            return await _async_wrapper(weakref.ref(self), *args, fetch=fetch, **kwargs)

        return inner

    return _decorator
