from cmath import log
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from threading import Thread
from time import sleep, time
from devops_sccs.atscached import atscached, CacheInfo


def test_returns_expected_value():
    @atscached()
    def fn():
        return "test"

    assert fn() == "test"


async def test_returns_expected_value_async():
    @atscached()
    async def fn():
        return "test"

    assert await fn() == "test"


def test_returns_cached_value():
    @atscached()
    def fn(*a):
        return time()

    a = fn(0)
    b = fn(0)
    assert a == b

    c = fn(1)
    assert a != c


def test_expired_returns_new_value():
    ttl = 0.1

    @atscached(ttl=ttl)
    def fn():
        return time()

    a = fn()
    sleep(ttl + 0.1)
    b = fn()
    assert a != b


def test_cache_info():
    maxsize = 10
    ttl = 0.1

    @atscached(ttl=ttl, maxsize=maxsize)
    def fn(*a):
        return "test"

    fn()  # cache miss == 1
    fn()  # cache hit == 1
    sleep(ttl + 0.1)
    fn()  # cache miss == 2 (expired)
    fn()  # cache hit == 2
    fn("a")  # cache miss == 3 (new key)

    assert fn.cache_info() == CacheInfo(hits=2, misses=3, maxsize=maxsize, currsize=2)


def test_cache_clear():
    maxsize = 10

    @atscached(maxsize=maxsize)
    def fn(*a):
        return time()

    a = fn()
    fn.cache_clear()
    b = fn()

    assert a != b

    fn.cache_clear()
    assert fn.cache_info() == CacheInfo(hits=0, misses=0, maxsize=maxsize, currsize=0)


def test_cache_overflow():
    maxsize = 10

    @atscached(maxsize=maxsize)
    def fn(*a):
        return time()

    # fill cache
    l = [fn(i) for i in range(maxsize)]

    assert fn.cache_info() == CacheInfo(
        hits=0, misses=maxsize, maxsize=maxsize, currsize=maxsize
    )

    # add one more item
    fn(maxsize)

    # cache size should be the same
    assert fn.cache_info().currsize == maxsize

    # oldest item should be gone
    assert l[0] != fn(0)


def test_miss_callback():
    count = 0

    def miss_callback(result):
        nonlocal count
        count += 1
        return result

    @atscached(miss_callback=miss_callback)
    def fn(*a):
        return time()

    a = fn()  # cache miss == 1
    b = fn()

    # cache should still function normally
    assert a == b

    fn(0)  # cache miss == 2

    # miss callback should have been called twice
    assert count == 2


def test_concurrency():
    # test that concurrent access to the cache doesn't cause problems
    maxsize = 10
    ttl = 0.1
    count = 0

    @atscached(maxsize=maxsize, ttl=ttl)
    def fn(*a):
        nonlocal count
        count += 1

    with ThreadPoolExecutor(max_workers=maxsize) as executor:

        def run(i):
            a = i % 2  # only two keys
            if i == 3 or i == 4:  # two of them cause a cache miss (one for each key)
                sleep(ttl + 0.1)
            fn(a)

        executor.map(run, range(maxsize))

    # expect fn to have been called twice (once for each key) plus twice more for the two cache misses
    assert count == 4
    assert fn.cache_info().currsize == 2
    assert fn.cache_info().hits == maxsize - count
    assert fn.cache_info().misses == count


def test_fetch_flag():
    @atscached()
    def fn(*a):
        return time()

    a = fn(0)
    b = fn(0, fetch=True)

    assert a != b
