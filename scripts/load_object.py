import asyncio
import json
import sys

from dotenv import load_dotenv

from tracecat.ee.store.models import ActionResultHandle, WorkflowResultHandle
from tracecat.ee.store.service import get_store

load_dotenv()
path = sys.argv[1]


async def main():
    store = get_store()
    if path.endswith("_result.json"):
        handle = WorkflowResultHandle.from_key(path)
    else:
        handle = ActionResultHandle.from_key(path)
    return await store.load_task_result(handle)


result = asyncio.run(main())
print(json.dumps(result, indent=2))
