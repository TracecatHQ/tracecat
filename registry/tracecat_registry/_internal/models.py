from typing import Annotated

from pydantic import BaseModel, StringConstraints

SecretName = Annotated[str, StringConstraints(pattern=r"[a-z0-9_]+")]
"""Validator for a secret name. e.g. 'aws_access_key_id'"""

SecretKey = Annotated[str, StringConstraints(pattern=r"[a-zA-Z0-9_]+")]
"""Validator for a secret key. e.g. 'access_key_id'"""


class RegistrySecret(BaseModel):
    name: SecretName
    keys: list[SecretKey]
