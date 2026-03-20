from collections.abc import Iterable as _Iterable
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf.internal import containers as _containers

DESCRIPTOR: _descriptor.FileDescriptor

class Password(_message.Message):
    __slots__ = ("email", "hash", "username", "user_id")
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    HASH_FIELD_NUMBER: _ClassVar[int]
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    email: str
    hash: bytes
    username: str
    user_id: str
    def __init__(
        self,
        email: str | None = ...,
        hash: bytes | None = ...,
        username: str | None = ...,
        user_id: str | None = ...,
    ) -> None: ...

class CreatePasswordReq(_message.Message):
    __slots__ = ("password",)
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    password: Password
    def __init__(self, password: Password | _Mapping | None = ...) -> None: ...

class CreatePasswordResp(_message.Message):
    __slots__ = ("already_exists",)
    ALREADY_EXISTS_FIELD_NUMBER: _ClassVar[int]
    already_exists: bool
    def __init__(self, already_exists: bool = ...) -> None: ...

class UpdatePasswordReq(_message.Message):
    __slots__ = ("email", "new_hash", "new_username")
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    NEW_HASH_FIELD_NUMBER: _ClassVar[int]
    NEW_USERNAME_FIELD_NUMBER: _ClassVar[int]
    email: str
    new_hash: bytes
    new_username: str
    def __init__(
        self,
        email: str | None = ...,
        new_hash: bytes | None = ...,
        new_username: str | None = ...,
    ) -> None: ...

class UpdatePasswordResp(_message.Message):
    __slots__ = ("not_found",)
    NOT_FOUND_FIELD_NUMBER: _ClassVar[int]
    not_found: bool
    def __init__(self, not_found: bool = ...) -> None: ...

class DeletePasswordReq(_message.Message):
    __slots__ = ("email",)
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    email: str
    def __init__(self, email: str | None = ...) -> None: ...

class DeletePasswordResp(_message.Message):
    __slots__ = ("not_found",)
    NOT_FOUND_FIELD_NUMBER: _ClassVar[int]
    not_found: bool
    def __init__(self, not_found: bool = ...) -> None: ...

class ListConnectorReq(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class Connector(_message.Message):
    __slots__ = ("id", "type", "name", "config", "grant_types")
    ID_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    GRANT_TYPES_FIELD_NUMBER: _ClassVar[int]
    id: str
    type: str
    name: str
    config: bytes
    grant_types: _containers.RepeatedScalarFieldContainer[str]
    def __init__(
        self,
        id: str | None = ...,
        type: str | None = ...,
        name: str | None = ...,
        config: bytes | None = ...,
        grant_types: _Iterable[str] | None = ...,
    ) -> None: ...

class ListConnectorResp(_message.Message):
    __slots__ = ("connectors",)
    CONNECTORS_FIELD_NUMBER: _ClassVar[int]
    connectors: _containers.RepeatedCompositeFieldContainer[Connector]
    def __init__(
        self, connectors: _Iterable[Connector | _Mapping] | None = ...
    ) -> None: ...
