from pydantic import UUID4, BaseModel


class ExecutorSyncInput(BaseModel):
    repository_id: UUID4
