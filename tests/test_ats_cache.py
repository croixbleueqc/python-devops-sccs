from concurrent.futures import ThreadPoolExecutor
from time import sleep, time

from devops_sccs.ats_cache import CacheInfo, ats_cache


def test_returns_expected_value():
    @ats_cache()
    def fn():
        return "test"

    assert fn() == "test"


async def test_returns_expected_value_async():
    @ats_cache()
    async def fn():
        return "test"

    assert await fn() == "test"


def test_returns_cached_value():
    @ats_cache()
    def fn(*a):
        return time()

    a = fn(0)
    b = fn(0)

    assert a == b

    c = fn(1)

    assert a != c


def test_expired_returns_new_value():
    ttl = 0.1

    @ats_cache(ttl=ttl)
    def fn():
        return time()

    a = fn()
    sleep(ttl + 0.1)
    b = fn()

    assert a != b


def test_cache_info():
    maxsize = 10
    ttl = 0.1

    @ats_cache(ttl=ttl, maxsize=maxsize)
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

    @ats_cache(maxsize=maxsize)
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

    @ats_cache(maxsize=maxsize)
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

    @ats_cache(miss_callback=miss_callback)
    def fn(*a):
        return time()

    a = fn()  # cache miss == 1
    b = fn()

    # cache should still function normally
    assert a == b

    fn(0)  # cache miss == 2

    # miss callback should have been called twice
    assert count == 2


def test_concurrent_reads():
    # test that concurrent reads to the cache don't cause problems

    maxsize = 10
    ttl = 0.1

    @ats_cache(maxsize=maxsize, ttl=ttl)
    def fn(*a):
        return time()

    with ThreadPoolExecutor(max_workers=maxsize) as executor:

        # run the function concurrently with the same key
        # the expectation is that there will be exactly one cache miss
        # and maxsize - 1 cache hits
        executor.map(fn, [0 for _ in range(maxsize)])

    info = fn.cache_info()

    assert info.currsize == 1
    assert info.hits == maxsize - 1
    assert info.misses == 1


def test_concurrent_writes():
    # test that concurrent writes to the cache don't cause problems

    maxsize = 10

    # no expiry == 100% cache writes (expiries are handled by the cache itself)
    ttl = 0.0

    @ats_cache(maxsize=maxsize, ttl=ttl)
    def fn(*a):
        return time()

    with ThreadPoolExecutor(max_workers=maxsize) as executor:

        # run the function concurrently with the same key
        # the expectation is that there will be exactly maxsize cache misses
        # and zero cache hits
        executor.map(fn, [0 for _ in range(maxsize)])

    info = fn.cache_info()

    assert info.currsize == 1
    assert info.hits == 0
    assert info.misses == maxsize


def test_concurrency_global():
    # test that the cache can handle concurrent reads and writes to a global/nonlocal variable

    maxsize = 10
    ttl = 0.1

    nonlocalvar = 0

    @ats_cache(maxsize=maxsize, ttl=ttl)
    def fn(*a):
        nonlocal nonlocalvar
        nonlocalvar += 1

    with ThreadPoolExecutor(max_workers=maxsize) as executor:

        def run(i):
            a = i % 2  # only two keys
            if i == 3 or i == 4:  # two inputs cause a cache miss (one for each key)
                sleep(ttl + 0.1)
            fn(a)

        executor.map(run, range(maxsize))

    # expect the nonlocal variable to be incremented exactly four times
    # (once for each key + once per cache miss)
    assert nonlocalvar == 4

    info = fn.cache_info()

    assert info.currsize == 2
    assert info.hits == maxsize - nonlocalvar
    assert info.misses == nonlocalvar


def test_shared_variable():
    # test that two functions sharing a variable don't cause problems

    maxsize = 10
    ttl = 0.1

    nonlocalvar = 0

    @ats_cache(maxsize=maxsize, ttl=ttl)
    def fn_a(*a):
        nonlocal nonlocalvar
        nonlocalvar += 1

    @ats_cache(maxsize=maxsize, ttl=ttl)
    def fn_b(*a):
        nonlocal nonlocalvar
        nonlocalvar -= 1

    with ThreadPoolExecutor(max_workers=maxsize) as executor:
        executor.map(fn_a, range(maxsize))
        executor.map(fn_b, range(maxsize))

    # expect the nonlocal variable to have been incremented and decremented an equal number of times
    assert nonlocalvar == 0

    info_a = fn_a.cache_info()
    info_b = fn_b.cache_info()

    assert info_a.currsize == maxsize and info_b.currsize == maxsize
    assert info_a.hits == 0 and info_b.hits == 0
    assert info_a.misses == maxsize and info_b.misses == maxsize


def test_forced_fetch():
    # the fetch argument should cause the cache to invalidate the cache entry

    @ats_cache()
    def fn(*a):
        return time()

    a = fn(0)
    b = fn(0, fetch=True)

    assert a != b


def test_arg_types():
    # test that the cache can handle different types of arguments, including unhashable types

    @ats_cache()
    def fn(*a):
        return time()

    try:
        fn(0)  # int
        fn(0.0)  # float
        fn(complex(1, 2))  # complex
        fn([])  # list
        fn(())  # tuple
        fn(range(10))  # range
        fn("")  # str
        fn(bytes(10))  # bytes
        fn(bytearray(10))  # bytearray
        fn(set())  # set
        fn(frozenset())  # frozenset
        fn({})  # dict
        fn(slice(0, 10))  # slice
        fn(type(0))  # type
        fn(None)  # NoneType
        fn(...)  # Ellipsis
        fn(True)  # bool
        fn(NotImplemented)  # NotImplementedType

    except Exception:
        assert False

    assert True
