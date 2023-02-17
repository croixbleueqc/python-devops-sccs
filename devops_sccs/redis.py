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
        try:
            serialized = pickle.dumps(value)
        except AttributeError:
            serialized = dill.dumps(value)  # slower than pickle, but can handle more types
        return serialized

    @staticmethod
    def deserialize(value) -> Any:
        if value is None:
            return None
        try:
            deserialized = pickle.loads(value)
        except (pickle.UnpicklingError, AttributeError, TypeError):
            deserialized = dill.loads(value)
        return deserialized


class RedisCache:
    """Basic singleton wrapper for redis client.  Pickles/Dills everything."""
    _cache = None
    redis = None
    _is_initialized = False

    def __new__(cls, *args, **kwargs):
        if not cls._cache:
            cls._cache = super().__new__(cls)
        return cls._cache

    def init(self):
        if self._is_initialized:
            return

        try:
            redis_password = os.environ['REDIS_PASSWORD']
            redis_host = os.environ.get('REDIS_HOST', 'localhost')

            # redis_url = f'redis://:{redis_password}@{redis_host}:6379/0'
            self.redis = Redis(redis_host, password=redis_password, decode_responses=False)
            assert self.redis.ping()
        except Exception as e:
            logger.critical(e)
            self.redis = None
            raise e

        logger.debug("REDIS CACHE initialized")
        self._is_initialized = True

    def set(self, key, value, ttl=timedelta(hours=1)) -> bool:
        value = Serializer.serialize(value)
        success = self.redis.set(key, value, ex=ttl)
        if success:
            logger.debug(f'REDIS CACHE SET for "{key}"')
        return success

    def get(self, key, default=None) -> Any:
        value = self.redis.get(key)
        if value is None:
            logger.debug(f'REDIS CACHE MISS for "{key}"')
            return default
        value = Serializer.deserialize(value)
        logger.debug(f'REDIS CACHE HIT for "{key}"')
        return value

    def exists(self, key) -> bool:
        return self.redis.exists(key) > 0

    def delete(self, *keys) -> int:
        n = self.redis.delete(*keys)
        logger.debug(f"REDIS CACHE DELETE {n} keys for {keys}")
        return n

    def delete_namespace(self, namespace) -> int:
        n = 0
        for key in self.redis.scan_iter(f"{namespace}*"):
            n += self.delete(key)
        return n

    def clear(self):
        logger.debug("REDIS CACHE CLEAR")
        self.redis.flushall()

    @property
    def initialized(self):
        return self._is_initialized


def cache_async(
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

            cached = _cache.get(_key)
            if cached is not None:
                return cached

            result = await method(_self(), *args, **kwargs)
            _cache.set(_key, result, ttl=ttl)
            return result

        @functools.wraps(method)
        async def inner(self, *args, fetch=False, **kwargs):
            return await _async_wrapper(weakref.ref(self), *args, fetch=fetch, **kwargs)

        return inner

    return _decorator


def cache_sync(
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
        def _wrapper(_self, *args, fetch: bool, **kwargs):
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

            cached = _cache.get(_key)
            if cached is not None:
                return cached

            result = method(_self(), *args, **kwargs)
            _cache.set(_key, result, ttl=ttl)
            return result

        @functools.wraps(method)
        def inner(self, *args, fetch=False, **kwargs):
            return _wrapper(weakref.ref(self), *args, fetch=fetch, **kwargs)

        return inner

    return _decorator
