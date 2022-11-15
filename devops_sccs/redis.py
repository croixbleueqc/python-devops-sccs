import functools
import os
import pickle
import weakref
from datetime import timedelta
from typing import Any

import dill
from loguru import logger
from redis import Redis

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
        self.pickleclient = None
        self._is_initialized = False

    def init(self):
        if self._is_initialized:
            return

        try:
            redis_password = os.environ['REDIS_PASSWORD']
            redis_host = os.environ.get('REDIS_HOST', 'localhost')

            # redis_url = f'redis://:{redis_password}@{redis_host}:6379/0?decode_responses=True'
            self.client = Redis(redis_host, password=redis_password, decode_responses=True)
            assert self.client.ping()
            self.pickleclient = Redis(redis_host, password=redis_password, decode_responses=False)
            assert self.pickleclient.ping()
        except Exception as e:
            logger.critical(e)
            self.client = None
            raise e

        self._is_initialized = True

    def set(self, key, value, ttl=timedelta(hours=1)) -> bool:
        value = Serializer.serialize(value)
        if Serializer.needs_pickling(value):
            return self.pickleclient.set(key, value, ex=ttl)
        return self.client.set(key, value, ex=ttl)

    def get(self, key) -> Any:
        try:
            value = self.client.get(key)
        except UnicodeDecodeError:
            value = self.pickleclient.get(key)
        return Serializer.deserialize(value)

    def exists(self, key) -> bool:
        return self.client.exists(key) or self.pickleclient.exists(key)

    def delete(self, *keys, client=None) -> int:
        if client is None:
            n = self.client.delete(*keys)
            n += self.pickleclient.delete(*keys)
        else:
            n = client.delete(*keys)

    async def hexists(self, key, field) -> bool:
        return await self.client.hexists(key, field)

    async def hset(self, key, field, value):
        value = Serializer.serialize(value)
        return await self.client.hset(key, field, value)

    async def hget(self, key, field, default=None):
        value = await self.client.hget(key, field)
        if value is None:
            return default
        return Serializer.deserialize(value)

    async def hgetall(self, key):
        values = await self.client.hgetall(key)
        return {int(k): Serializer.deserialize(v) for k, v in values.items()}

    async def hpop(self, key, field) -> Any:
        value = await self.hget(key, field)
        await self.client.hdel(key, field)
        return value

    async def hkeys(self, key) -> list[int]:
        return list(map(lambda k: int(k), await self.client.hkeys(key)))

    async def hscan_iter(self, key):
        async for key, value in self.client.hscan_iter(key):
            yield int(key), Serializer.deserialize(value)

    def delete_namespace(self, namespace) -> int:
        n = 0
        for key in self.client.scan_iter(f"{namespace}*"):
            n += self.delete(key, client=self.client)
        for key in self.pickleclient.scan_iter(f"{namespace}*"):
            n += self.delete(key, client=self.pickleclient)
        return n

    def clear(self):
        self.client.flushall()
        self.pickleclient.flushall()

    @property
    def initialized(self):
        return self._is_initialized


def cache(
        ttl: timedelta,
        key: str | CacheKeyFn | None = None,
        namespace: str = "",
        ):
    """Wrapper for caching **method**  results in redis.

    Args:
        ttl: time to live for the cached value
        key: key to use for the cache. Can be an static string, a function or None. In the case of
        a function, it is expected to have some, or all of the same arguments as the wrapped method.
        namespace: prefix to use for the cache key. Useful for differentiating between different
        instances of the same class, for example.
    """

    def _decorator(method):
        async def _async_wrapper(_self, *args, fetch: bool, **kwargs):
            _cache = RedisCache()
            if not _cache.initialized:
                _cache.init()

            # key
            _key = None
            if key is None:
                _key = CacheKeyFn.make_default_key(method.__name__, *args, **kwargs)
            elif isinstance(key, str):
                _key = key
            elif isinstance(key, CacheKeyFn):
                _key = key.infer_from_orig(method, *args, **kwargs)
            if _key is None:
                raise ValueError('Invalid key')

            if namespace:
                _key = CacheKeyFn.prepend_namespace(namespace, _key)

            if fetch:
                logger.debug(f'REDIS CACHE: fetch flag set, deleting cached value')
                n = _cache.delete(_key)
                logger.debug(f'REDIS CACHE: deleted {n} keys')

            cached = _cache.get(_key)
            if cached is not None:
                logger.debug(f'REDIS CACHE HIT for key {_key}')
                return cached

            logger.debug(f'REDIS CACHE MISS for key {_key}')
            result = await method(_self(), *args, **kwargs)
            _cache.set(_key, result, ttl=ttl)
            return result

        @functools.wraps(method)
        async def inner(self, *args, fetch=False, **kwargs):
            return await _async_wrapper(weakref.ref(self), *args, fetch=fetch, **kwargs)

        return inner

    return _decorator
