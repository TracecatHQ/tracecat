from pydantic import BaseModel


class EditorParamRead(BaseModel):
    name: str
    type: str
    optional: bool


class EditorFunctionRead(BaseModel):
    name: str
    description: str
    parameters: list[EditorParamRead]
    return_type: str


class EditorActionRead(BaseModel):
    type: str
    ref: str
    description: str
