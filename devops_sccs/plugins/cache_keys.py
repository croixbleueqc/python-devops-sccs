from typing import Callable

from devops_sccs.plugins.bitbucketcloud import BitbucketCloud


class CacheKeyFn:
    """Functions to return cache keys based on the arguments passed to the functions found
    in SccsApi (or in a plugin).
    If a method is in SccsApi, but not here, it probably doesn't need a cache key function"""

    def __init__(self, name: str, arg_names=None, kwarg_names=None):
        if arg_names is None:
            arg_names = []
        if kwarg_names is None:
            kwarg_names = []
        self.name = name
        self.arg_names = arg_names
        self.kwarg_names = kwarg_names

    @classmethod
    def from_fn(cls, fn, arg_names: list[str], kwarg_names: list[str]):
        return cls(fn.__name__, arg_names, kwarg_names)

    def __call__(self, *args, **kwargs):
        key = self.name

        hasargs = len(args) > 0 or len(kwargs) > 0

        if hasargs:
            key += '('
            if len(args) > 0:
                key += ', '.join([str(a) for a in args])
            if len(kwargs) > 0:
                key += ', '.join([f"{k}={v}" for k, v in kwargs.items()])
            key += ')'

        return key

    def infer_from_orig(self, fn: Callable, *args, **kwargs):
        """Returns a cache key. Called with the same arguments as the function it's a key for.
        Discards unwanted arguments automagically."""

        # check that the number of args and kwargs is correct
        if len(args) < len(self.arg_names) or len(kwargs) < len(self.kwarg_names):
            raise ValueError(
                f"Cache key function '{self.name}' called with too few arguments"
                )

        # introspect the function signature to see what positional arguments from the original
        # should be passed to the key function
        original_arg_names = tuple(
            filter(
                lambda a: a != 'self',
                fn.__code__.co_varnames[:fn.__code__.co_argcount]
                )
            )  # 'self' is not a positional arg

        key_args = tuple(
            args[i] for i, n in enumerate(original_arg_names) if
            n in self.arg_names and len(args) > i
            )
        key_kwargs = {arg: kwargs[arg] for arg in kwargs if arg in self.kwarg_names}

        return self(*key_args, **key_kwargs)


cache_key_fns = {
    "get_continuous_deployment_config": CacheKeyFn.from_fn(
        BitbucketCloud.get_continuous_deployment_config,
        ["repo_name"]
        )
    }
