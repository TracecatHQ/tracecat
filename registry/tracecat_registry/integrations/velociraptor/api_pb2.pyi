from collections.abc import Iterable as _Iterable
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf.internal import containers as _containers

DESCRIPTOR: _descriptor.FileDescriptor

class VQLRequest(_message.Message):
    __slots__ = ("Name", "VQL")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VQL_FIELD_NUMBER: _ClassVar[int]
    Name: str
    VQL: str
    def __init__(self, Name: str | None = ..., VQL: str | None = ...) -> None: ...

class VQLEnv(_message.Message):
    __slots__ = ("key", "value")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    key: str
    value: str
    def __init__(self, key: str | None = ..., value: str | None = ...) -> None: ...

class VQLCollectorArgs(_message.Message):
    __slots__ = (
        "env",
        "Query",
        "max_row",
        "max_wait",
        "ops_per_second",
        "org_id",
        "timeout",
    )
    ENV_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    MAX_ROW_FIELD_NUMBER: _ClassVar[int]
    MAX_WAIT_FIELD_NUMBER: _ClassVar[int]
    OPS_PER_SECOND_FIELD_NUMBER: _ClassVar[int]
    ORG_ID_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_FIELD_NUMBER: _ClassVar[int]
    env: _containers.RepeatedCompositeFieldContainer[VQLEnv]
    Query: _containers.RepeatedCompositeFieldContainer[VQLRequest]
    max_row: int
    max_wait: int
    ops_per_second: float
    org_id: str
    timeout: int
    def __init__(
        self,
        env: _Iterable[VQLEnv | _Mapping] | None = ...,
        Query: _Iterable[VQLRequest | _Mapping] | None = ...,
        max_row: int | None = ...,
        max_wait: int | None = ...,
        ops_per_second: float | None = ...,
        org_id: str | None = ...,
        timeout: int | None = ...,
    ) -> None: ...

class VQLTypeMap(_message.Message):
    __slots__ = ("column", "type")
    COLUMN_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    column: str
    type: str
    def __init__(self, column: str | None = ..., type: str | None = ...) -> None: ...

class VQLResponse(_message.Message):
    __slots__ = (
        "Response",
        "Columns",
        "types",
        "query_id",
        "part",
        "Query",
        "timestamp",
        "total_rows",
        "log",
    )
    RESPONSE_FIELD_NUMBER: _ClassVar[int]
    COLUMNS_FIELD_NUMBER: _ClassVar[int]
    TYPES_FIELD_NUMBER: _ClassVar[int]
    QUERY_ID_FIELD_NUMBER: _ClassVar[int]
    PART_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    TOTAL_ROWS_FIELD_NUMBER: _ClassVar[int]
    LOG_FIELD_NUMBER: _ClassVar[int]
    Response: str
    Columns: _containers.RepeatedScalarFieldContainer[str]
    types: _containers.RepeatedCompositeFieldContainer[VQLTypeMap]
    query_id: int
    part: int
    Query: VQLRequest
    timestamp: int
    total_rows: int
    log: str
    def __init__(
        self,
        Response: str | None = ...,
        Columns: _Iterable[str] | None = ...,
        types: _Iterable[VQLTypeMap | _Mapping] | None = ...,
        query_id: int | None = ...,
        part: int | None = ...,
        Query: VQLRequest | _Mapping | None = ...,
        timestamp: int | None = ...,
        total_rows: int | None = ...,
        log: str | None = ...,
    ) -> None: ...

class VFSFileBuffer(_message.Message):
    __slots__ = ("client_id", "offset", "length", "data", "components")
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    LENGTH_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    COMPONENTS_FIELD_NUMBER: _ClassVar[int]
    client_id: str
    offset: int
    length: int
    data: bytes
    components: _containers.RepeatedScalarFieldContainer[str]
    def __init__(
        self,
        client_id: str | None = ...,
        offset: int | None = ...,
        length: int | None = ...,
        data: bytes | None = ...,
        components: _Iterable[str] | None = ...,
    ) -> None: ...
