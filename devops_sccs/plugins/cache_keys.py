import json
import re
from typing import Callable


class CacheKeyFn:
    """Functions to return cache keys based on the arguments passed to the functions found
    in SccsApi (or in a plugin).
    If a method is in SccsApi, but not here, it probably doesn't need a cache key function"""

    def __init__(
            self,
            name: str,
            arg_names: list[str] = None,
            kwarg_names: list[str] = None,
            namespace: str = None,
            ):
        if arg_names is None:
            arg_names = []
        if kwarg_names is None:
            kwarg_names = []
        self.name = name
        self.arg_names = arg_names
        self.kwarg_names = kwarg_names
        self.namespace = namespace

    @classmethod
    def from_fn(cls, fn, arg_names: list[str], kwarg_names: list[str], namespace: str = None):
        return cls(fn.__name__, arg_names, kwarg_names)

    def __call__(self, *args, **kwargs):
        key = CacheKeyFn.make_default_key(self.name, *args, **kwargs)

        if self.namespace is not None:
            key = CacheKeyFn.prepend_namespace(self.namespace, key)

        return key

    def infer_from_orig(self, fn: Callable, *args, **kwargs):
        """Returns a cache key. Called with the same arguments as the function it's a key for.
        Discards unwanted arguments automagically."""

        # check that the number of args and kwargs is correct
        if len(args) + len(kwargs) < len(self.arg_names) + len(self.kwarg_names):
            raise ValueError(
                f"Cache key function '{self.name}' called with too few arguments"
                )

        original_arg_names = tuple(
            filter(
                lambda a: a != 'self',
                fn.__code__.co_varnames
                )
            )

        # get positional args
        key_fn_args = ()
        for i, n in enumerate(original_arg_names):
            if n in self.arg_names:
                key_fn_args += (args[i],)
                # some positional args may have been passed as kwargs
                if n in kwargs:
                    key_fn_args += (kwargs[n],)
                    del kwargs[n]

        # get keyword args
        key_fn_kwargs = {arg: kwargs[arg] for arg in kwargs if
                         arg in self.kwarg_names}

        return self(*key_fn_args, **key_fn_kwargs)

    @staticmethod
    def make_default_key(name: str, *args: tuple, hash_it=False, **kwargs: dict):
        key = name

        hasargs = len(args) > 0 or len(kwargs) > 0

        def stringify(v) -> str:
            if isinstance(v, str):
                return v
            elif isinstance(v, (int, float, complex, bytes, bool)):
                return str(v)
            else:
                try:
                    return json.dumps(v)
                except TypeError:
                    s = str(v).replace('\n', '')  # remove newlines to not clutter logs
                    regex = re.compile(r'[\s]{2,}')
                    s = re.sub(regex, ' ', s)  # replace multiple spaces with a single one
                    return s

        if hasargs:
            key += '('
            key += ', '.join([stringify(a) for a in args])
            key += ', '.join([f'{k}={stringify(v)}' for k, v in kwargs.items()])
            key += ')'

        return key if not hash_it else hash(key)

    @staticmethod
    def prepend_namespace(namespace: str, key: str):
        return f"{namespace}::{key}"


cache_key_fns = {
    "get_continuous_deployment_config": CacheKeyFn(
        "get_continuous_deployment_config",
        ["repo_slug", "environments"],
        ),
    "get_deployment_status": CacheKeyFn(
        name="get_deployment_status",
        kwarg_names=["slug", "environment"]
        )
    }
