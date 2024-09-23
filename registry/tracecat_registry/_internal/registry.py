from collections.abc import Callable
from types import FunctionType

from tracecat_registry._internal.constants import DEFAULT_NAMESPACE
from tracecat_registry._internal.models import RegistrySecret


def register(
    *,
    default_title: str | None = None,
    display_group: str | None = None,
    namespace: str = DEFAULT_NAMESPACE,
    description: str,
    secrets: list[RegistrySecret] | None = None,
    version: str | None = None,
    include_in_schema: bool = True,
) -> Callable[[FunctionType], FunctionType]:
    """Decorator factory to register a new UDF (User-Defined Function) with additional parameters.

    This method creates a decorator that can be used to register a function as a UDF in the Tracecat system.
    It handles the registration process, including metadata assignment, argument validation, and execution wrapping.

    Parameters
    ----------
    default_title : str | None, optional
        The default title for the UDF in the catalog, by default None.
    display_group : str | None, optional
        The group under which the UDF should be displayed in the catalog, by default None.
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
    Callable[[FunctionType], FunctionType]
        A decorator function that registers the decorated function as a UDF.

    Notes
    -----
    The decorated function will be wrapped to handle argument validation and secret injection.
    Both synchronous and asynchronous functions are supported.
    """

    def decorator_register(fn: FunctionType) -> FunctionType:
        """The decorator function to register a new UDF.

        This inner function handles the actual registration process for a given function.

        Parameters
        ----------
        fn : FunctionType
            The function to be registered as a UDF.

        Returns
        -------
        FunctionType
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
                "include_in_schema": include_in_schema,
                "namespace": namespace,
                "version": version,
                "description": description,
                "secrets": secrets,
            },
        )
        return fn

    return decorator_register
