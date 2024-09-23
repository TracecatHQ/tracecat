import asyncio
import inspect
from types import CoroutineType, FunctionType, MethodType
from typing import Any, Generic, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic_core import ValidationError
from tracecat_registry import RegistrySecret, RegistryValidationError

from tracecat import config
from tracecat.auth.sandbox import AuthSandbox
from tracecat.db.schemas import UDFSpec
from tracecat.identifiers import OwnerID
from tracecat.logger import logger
from tracecat.registry.models import ArgsClsT, ArgsT, RegisteredUDFMetadata


class UDFSchema(BaseModel):
    args: dict[str, Any]
    rtype: dict[str, Any] | None
    secrets: list[RegistrySecret] | None
    version: str | None
    description: str
    namespace: str
    key: str
    metadata: RegisteredUDFMetadata


class RegisteredUDF(BaseModel, Generic[ArgsClsT]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    fn: FunctionType | MethodType | CoroutineType
    key: str
    description: str
    namespace: str
    version: str | None = None
    secrets: list[RegistrySecret] | None = None
    args_cls: ArgsClsT
    args_docs: dict[str, str] = Field(default_factory=dict)
    rtype_cls: Any | None = None
    rtype_adapter: TypeAdapter[Any] | None = None
    metadata: RegisteredUDFMetadata = Field(default_factory=dict)

    @property
    def is_async(self) -> bool:
        return inspect.iscoroutinefunction(self.fn)

    def construct_schema(self) -> dict[str, Any]:
        return UDFSchema(
            args=self.args_cls.model_json_schema(),
            rtype=None if not self.rtype_adapter else self.rtype_adapter.json_schema(),
            secrets=self.secrets,
            version=self.version,
            description=self.description,
            namespace=self.namespace,
            key=self.key,
            metadata=self.metadata,
        ).model_dump(mode="json")

    def validate_args[T](self, *args, **kwargs) -> T:
        """Validate the input arguments for a UDF.

        Checks:
        1. The UDF must be called with keyword arguments only.
        2. The input arguments must be validated against the UDF's model.
        """
        if len(args) > 0:
            raise RegistryValidationError(
                "UDF must be called with keyword arguments.", key=self.key
            )

        # Validate the input arguments, fail early if the input is invalid
        # Note that we've added TemplateValidator to the list of validators
        # so template expressions will pass args model validation
        try:
            # Note that we're allowing type coercion for the input arguments
            # Use cases would be transforming a UTC string to a datetime object
            # We return the validated input arguments as a dictionary
            validated: BaseModel = self.args_cls.model_validate(kwargs)
            return cast(T, validated.model_dump())
        except ValidationError as e:
            logger.error(f"Validation error for UDF {self.key!r}. {e.errors()!r}")
            raise RegistryValidationError(
                f"Validation error for UDF {self.key!r}. {e.errors()!r}",
                key=self.key,
                err=e,
            ) from e
        except Exception as e:
            raise RegistryValidationError(
                f"Unexpected error when validating input arguments for UDF {self.key!r}. {e}",
                key=self.key,
            ) from e

    async def run_async(
        self, *, args: ArgsT, context: dict[str, Any] | None = None
    ) -> Any:
        """Run a UDF async.

        You only need to pass `base_context` if the UDF is a template.
        """
        validated_args = self.validate_args(**args)
        if self.metadata.get("is_template"):
            kwargs = cast(
                ArgsT, {"args": validated_args, "base_context": context or {}}
            )
        else:
            kwargs = validated_args
        logger.warning("Running UDF async", kwargs=kwargs)
        secret_names = [secret.name for secret in self.secrets or []]
        # XXX(concurrency): AuthSandbox isn't threadsafe. NEEDS TO BE FIXED
        async with AuthSandbox(secrets=secret_names, target="env"):
            if self.is_async:
                return await self.fn(**kwargs)
            return await asyncio.to_thread(self.fn, **kwargs)

    def run_sync(self, *, args: ArgsT, context: dict[str, Any] | None = None) -> Any:
        loop = asyncio.get_event_loop()
        loop.set_task_factory(asyncio.eager_task_factory)
        try:
            return loop.run_until_complete(self.run_async(args=args, context=context))
        except asyncio.CancelledError:
            logger.error(f"UDF {self.key!r} was cancelled")
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception as e:
            logger.error(f"Error running UDF {self.key!r}: {e}")
            raise

    def to_udf_spec(
        self, owner_id: OwnerID = config.TRACECAT__DEFAULT_USER_ID
    ) -> UDFSpec:
        return UDFSpec(
            owner_id=owner_id,
            key=self.key,
            description=self.description,
            namespace=self.namespace,
            version=self.version,
            json_schema=self.construct_schema(),
            meta=self.metadata,
        )
