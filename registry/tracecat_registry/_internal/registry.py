from collections.abc import Callable
from typing import ParamSpec, TypeVar

from tracecat_registry._internal.constants import DEFAULT_NAMESPACE
from tracecat_registry._internal.models import RegistrySecret

P = ParamSpec("P")
R = TypeVar("R")


def register(
    *,
    default_title: str | None = None,
    display_group: str | None = None,
    doc_url: str | None = None,
    author: str | None = None,
    deprecated: str | None = None,
    namespace: str = DEFAULT_NAMESPACE,
    description: str,
    secrets: list[RegistrySecret] | None = None,
    include_in_schema: bool = True,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator factory to register a new UDF (User-Defined Function) with additional parameters.

    This method creates a decorator that can be used to register a function as a UDF in the Tracecat system.
    It handles the registration process, including metadata assignment, argument validation, and execution wrapping.

    Parameters
    ----------
    default_title : str | None, optional
        The default title for the UDF in the catalog, by default None.
    display_group : str | None, optional
        The group under which the UDF should be displayed in the catalog, by default None.
    doc_url : str | None, optional
        The URL to the documentation for the UDF, by default None.
    author : str | None, optional
        The author of the UDF, by default None.
    deprecated : str | None, optional
        The deprecation message for the UDF, by default None.
    namespace : str, optional
        The namespace to register the UDF under, by default 'core'.
    description : str
        A detailed description of the UDF's purpose and functionality.
    secrets : list[RegistrySecret] | None, optional
        A list of secret keys required by the UDF, by default None.
    version : str | None, optional
        The version of the UDF, by default None.
    include_in_schema : bool, optional
        Whether to include this UDF in the API schema, by default True.

    Returns
    -------
    Callable[[Callable[P, R]], Callable[P, R]]
        A decorator function that registers the decorated function as a UDF.

    Notes
    -----
    The decorated function will be wrapped to handle argument validation and secret injection.
    Both synchronous and asynchronous functions are supported.
    """

    def decorator_register(fn: Callable[P, R]) -> Callable[P, R]:
        """The decorator function to register a new UDF.

        This inner function handles the actual registration process for a given function.

        Parameters
        ----------
        fn : Callable[P, R]
            The function to be registered as a UDF.

        Returns
        -------
        Callable[P, R]
            The wrapped and registered UDF function.

        Raises
        ------
        ValueError
            If the UDF key is already registered or if the provided object is not callable.
        """
        if not callable(fn):
            raise ValueError("The provided object is not callable.")

        key = f"{namespace}.{fn.__name__}"

        setattr(fn, "__tracecat_udf", True)
        setattr(fn, "__tracecat_udf_key", key)
        setattr(
            fn,
            "__tracecat_udf_kwargs",
            {
                "default_title": default_title,
                "display_group": display_group,
                "doc_url": doc_url,
                "author": author,
                "deprecated": deprecated,
                "include_in_schema": include_in_schema,
                "namespace": namespace,
                "description": description,
                "secrets": secrets,
            },
        )
        return fn

    return decorator_register
