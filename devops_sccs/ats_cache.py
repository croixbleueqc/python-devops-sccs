import logging
import time
import weakref
from collections import deque, namedtuple
from functools import wraps
from math import inf
from threading import Lock, RLock
from typing import Callable


class CacheError(Exception):
    pass


class CacheMiss(CacheError, KeyError):
    pass


class CacheExpired(CacheError, ValueError):
    pass


CacheItem = namedtuple("CacheItem", ("expiry", "value"))
CacheInfo = namedtuple("CacheInfo", ["hits", "misses", "maxsize", "currsize"])


def now():
    return time.time()


def makekey(args: tuple, kwargs: dict):
    key = args
    if kwargs:
        for item in kwargs.items():
            key += item
    try:
        hash_value = hash(key)
    except TypeError:
        return str(key)  # for unhashable types, just return the value of __str__()
    return hash_value


def ats_cache(
        maxsize: int | None = 1000,
        ttl: float = 3600.0,
        miss_callback: Callable = lambda _: _,
        ):
    """An asynchronous, thread-safe, TLRU cache. Used to decorate expensive **methods**.

    Arguments:
        maxsize: The maximum number of items to store in the cache.
        ttl: The time-to-live for items in the cache.
        miss_callback: A callback to call when a cache miss occurs. (in addition to the cached function itself)

    Returns:
        A decorator that can be used to cache the results of a function.

    Example:
        >>> class MyClass:
        >>>     @ats_cache()
        >>>     async def now(self, *args):
                    return time.time()
        >>> m = MyClass()
        >>> a = m.now(1, 2) # cache miss; function called
        >>> b = m.now(1, 2) # cache hit; function not called
        >>> assert a == b

    """

    if ttl <= 0.0:
        ttl = 0.0  # zero caching; always fetching

    r_lock = RLock()  # lock for cache reads
    w_lock = Lock()  # lock for cache writes

    cache: dict = {}
    cache_len = cache.__len__

    hits = misses = 0

    # LRU priority queue
    pq: deque[str | int] = deque(maxlen=maxsize)

    def wrapper(func):
        async def async_cache(_self, *args, fetch: bool, **kwargs):
            nonlocal cache, hits, misses

            key = makekey(args, kwargs)

            try:
                if key not in cache:
                    raise CacheMiss
                elif ttl != inf and cache[key].expiry < now():
                    raise CacheExpired
                elif fetch:
                    raise CacheExpired

                logging.debug(f"CACHE HIT for {func.__name__}({args}, {kwargs})")

                with r_lock:
                    hits += 1
                    pq.remove(key)
                    pq.appendleft(key)

            except CacheMiss:
                miss_callback(func.__name__)
                logging.debug(f"CACHE MISS for {func.__name__}({args}, {kwargs})")

                result = await func(_self(), *args, **kwargs)

                node = CacheItem(expiry=now() + ttl, value=result)

                with w_lock:
                    # remove oldest cache item if queue is full
                    if len(pq) == maxsize:
                        del cache[pq.pop()]

                    cache[key] = node
                    pq.appendleft(key)
                    misses += 1

            except CacheExpired:
                logging.debug(f"CACHE EXPIRED for {func.__name__}({args}, {kwargs})")

                result = await func(_self(), *args, **kwargs)

                node = CacheItem(expiry=now() + ttl, value=result)

                with w_lock:
                    cache[key] = node
                    pq.appendleft(key)
                    misses += 1

            return cache[key].value

        def cache_info():
            """Report cache statistics."""
            with r_lock:
                return CacheInfo(hits, misses, maxsize, cache_len())

        def cache_clear():
            """Clear the cache, and reset statistics."""
            with w_lock:
                cache.clear()
                pq.clear()
                nonlocal hits, misses
                hits = misses = 0

        @wraps(func)
        async def inner(self, *args, fetch: bool = False, **kwargs):
            return await async_cache(weakref.ref(self), *args, fetch=fetch, **kwargs)

        inner.cache_info = cache_info
        inner.cache_clear = cache_clear

        return inner

    return wrapper
