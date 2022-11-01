import functools
import os
import pickle
import weakref
from datetime import timedelta
from typing import Any

from loguru import logger
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.asyncio.connection import BlockingConnectionPool  # will block and wait rather than raise an exception if a client tries to connect and the pool is full


def needs_pickling(v):
    # pydantic models
    if issubclass(type(v), BaseModel):
        return True
    # recursive types (could contain pydantic models)
    # todo: probably better to check if the subtypes are pydantic models, but we don't pickle a lot of data so this is fine for now
    if isinstance(v, (list, tuple, dict)):
        return True
    return False


def needs_unpickling(v):
    if isinstance(v, bytes):
        return True
    return False


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
            logger.critical(f'Unable to talk to Redis: {e}')
            self.client = None
            raise e

        self._is_initialized = True

    async def set(self, key, value, ttl=timedelta(hours=1)) -> bool:
        if needs_pickling(value):
            value = pickle.dumps(value)
        return await self.client.set(key, value, ex=ttl)

    async def get(self, key) -> Any:
        value = await self.client.get(key)
        if needs_unpickling(value):
            return pickle.loads(value)
        return value

    async def exists(self, key) -> bool:
        return await self.client.exists(key)

    async def delete(self, *keys) -> int:
        return await self.client.delete(*keys)

    async def clear(self):
        await self.client.flushall()


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
        key: str | None = None,
        prefix: str = "",
        ):
    """Wrapper for caching **method**  results in redis."""

    def _decorator(method):
        async def _async_wrapper(_self, *args, fetch: bool, **kwargs):
            _cache = RedisCache()
            await _cache.init()

            _key = key or make_cache_key(method.__name__, args, kwargs)
            if prefix:
                _key = f'{prefix}{_key}'

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
