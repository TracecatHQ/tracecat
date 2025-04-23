import uuid

from pydantic import BaseModel

from tracecat.db.schemas import TaskStatus


# Task Groups
class TaskGroupReadMinimal(BaseModel):
    id: uuid.UUID
    name: str


class TaskGroupCreate(BaseModel):
    name: str


class TaskGroupUpdate(BaseModel):
    name: str | None = None


# TaskRead
class TaskRead(BaseModel):
    id: uuid.UUID
    name: str
    status: TaskStatus
    group_id: uuid.UUID
    group: TaskGroupReadMinimal


class TaskGroupRead(BaseModel):
    id: uuid.UUID
    name: str
    tasks: list[TaskRead]


# Tasks


class TaskCreate(BaseModel):
    name: str
    status: TaskStatus
    group_id: uuid.UUID


class TaskUpdate(BaseModel):
    name: str | None = None
    status: TaskStatus | None = None
