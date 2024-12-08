from pydantic import BaseModel


class ExecutorSyncInput(BaseModel):
    origin: str
