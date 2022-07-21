import inspect
import math
import time
from collections import deque, namedtuple
from functools import wraps
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

now = lambda: time.time()


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


def atscached(
    maxsize: int | None = 1000,
    ttl: float = 3600.0,
    miss_callback: Callable = lambda _: _,
):
    """An asynchronous, thread-safe, TLRU cache. Used to decorate expensive functions.

    Arguments:
        maxsize: The maximum number of items to store in the cache.
        ttl: The time-to-live for items in the cache.
        miss_callback: A callback to call when a cache miss occurs. (in addition to the cached function itself)

    Returns:
        A decorator that can be used to cache the results of a function.

    Example:
        >>> @atscached()
        >>> def expensive_function(a, b):
                return a + b
    """

    if ttl <= 0.0:
        ttl = 0.0  # zero caching; always fetching

    r_lock = RLock()  # lock for cache reads
    w_lock = Lock()  # lock for cache writes

    cache: dict = {}
    cache_len = cache.__len__

    hits = misses = 0

    # LRU priority queue
    pq: deque[str | int] = deque() if maxsize == math.inf else deque(maxlen=maxsize)

    def impl(func):
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args, fetch: bool = False, **kwargs):
                nonlocal cache, hits, misses

                key = makekey(args, kwargs)

                try:
                    if key not in cache:
                        raise CacheMiss
                    elif ttl != math.inf and cache[key].expiry < now():
                        raise CacheExpired
                    elif fetch:
                        raise CacheExpired

                    with r_lock:
                        hits += 1
                        pq.remove(key)
                        pq.appendleft(key)

                except CacheMiss:
                    result = await func(*args, **kwargs)

                    result = miss_callback(result)

                    node = CacheItem(expiry=now() + ttl, value=result)

                    with w_lock:
                        # remove oldest cache item if queue is full
                        if len(pq) == maxsize:
                            del cache[pq.pop()]

                        cache[key] = node
                        pq.appendleft(key)
                        misses += 1

                except CacheExpired:
                    result = await func(*args, **kwargs)

                    node = CacheItem(expiry=now() + ttl, value=result)

                    with w_lock:
                        cache[key] = node
                        pq.appendleft(key)
                        misses += 1

                return cache[key].value

            wrapper = async_wrapper
        else:

            @wraps(func)
            def sync_wrapper(*args, fetch: bool = False, **kwargs):
                nonlocal cache, hits, misses

                key = makekey(args, kwargs)

                try:
                    if key not in cache:
                        raise CacheMiss
                    elif ttl != math.inf and cache[key].expiry < now():
                        raise CacheExpired
                    elif fetch:
                        raise CacheExpired

                    with r_lock:
                        hits += 1
                        pq.remove(key)
                        pq.appendleft(key)

                except CacheMiss:
                    result = func(*args, **kwargs)

                    result = miss_callback(result)

                    node = CacheItem(expiry=now() + ttl, value=result)

                    with w_lock:
                        # remove oldest cache item if queue is full
                        if len(pq) == maxsize:
                            del cache[pq.pop()]

                        cache[key] = node
                        pq.appendleft(key)
                        misses += 1

                except CacheExpired:
                    result = func(*args, **kwargs)

                    node = CacheItem(expiry=now() + ttl, value=result)

                    with w_lock:
                        cache[key] = node
                        pq.appendleft(key)
                        misses += 1

                return cache[key].value

            wrapper = sync_wrapper

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

        wrapper.cache_info = cache_info
        wrapper.cache_clear = cache_clear

        return wrapper

    return impl
