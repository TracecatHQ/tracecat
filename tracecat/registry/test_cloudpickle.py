import asyncio

from pydantic import BaseModel

from tracecat.registry.executor import CloudpickleProcessPoolExecutor
from tracecat.registry.manager import RegistryManager
from tracecat.registry.store import Registry


async def main() -> None:
    # registry = registry_manager.get_registry()
    # udf: RegisteredUDF[Any] = registry.get("core.transform.reshape")

    manager = RegistryManager()

    class AddOneArgs(BaseModel):
        x: int

    reg1 = Registry(version="1")
    reg1.register_udf(
        fn=lambda x: x + 1,
        key="test.add_one",
        namespace="test",
        version="1",
        description="Test UDF",
        secrets=None,
        args_cls=AddOneArgs,
        args_docs={"x": "The number to add one to"},
        rtype=int,
        rtype_adapter=None,
        default_title="Add One",
        display_group="Test",
        include_in_schema=True,
        is_template=False,
        origin="base",
    )

    reg2 = Registry(version="2")
    reg2.register_udf(
        fn=lambda x: x + 100,
        key="test.add_one",
        namespace="test",
        version="2",
        description="Test UDF",
        secrets=None,
        args_cls=AddOneArgs,
        args_docs={"x": "Now we are adding 100"},
        rtype=int,
        rtype_adapter=None,
        default_title="Add One",
        display_group="Test",
        include_in_schema=True,
        is_template=False,
        origin="base",
    )
    manager.add_registry(reg1)
    manager.add_registry(reg2)

    udf1 = manager.get_action("test.add_one", version="1")
    udf2 = manager.get_action("test.add_one", version="2")
    loop = asyncio.get_event_loop()
    args = {"x": 1}
    context = {"some": "context"}
    with CloudpickleProcessPoolExecutor() as executor:
        result1 = await loop.run_in_executor(executor, udf1.run_sync, args, context)
        result2 = await loop.run_in_executor(executor, udf2.run_sync, args, context)

    print("result1", result1)
    print("result2", result2)


if __name__ == "__main__":
    import dotenv

    dotenv.load_dotenv()
    asyncio.run(main())
