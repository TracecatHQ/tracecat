from pydantic import BaseModel

from tracecat.registry.actions.models import RegistryActionRead


class RegistryRepositoryRead(BaseModel):
    version: str
    origin: str | None = None
    actions: list[RegistryActionRead]


class RegistryRepositoryReadMinimal(BaseModel):
    version: str
    origin: str | None = None


class RegistryRepositoryCreate(BaseModel):
    version: str
    origin: str | None = None


class RegistryRepositoryUpdate(BaseModel):
    name: str | None = None
    include_base: bool = True
    include_remote: bool = True
    include_templates: bool = True
