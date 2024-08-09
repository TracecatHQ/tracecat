from pydantic import BaseModel

# class CreateWorkspaceParams(BaseModel):
#     name: str
#     settings: dict[str, str] | None = None
#     owner_id: UUID4


class UpdateWorkspaceParams(BaseModel):
    name: str | None = None
    settings: dict[str, str] | None = None
