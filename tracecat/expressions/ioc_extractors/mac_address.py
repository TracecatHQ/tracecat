import re

from pydantic import BaseModel, ValidationError, field_validator
from pydantic_extra_types.mac_address import MacAddress

# MAC ADDRESS
MAC_REGEX = r"(?<![\d:A-Fa-f-])(?:[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}|[0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5})(?![\d:A-Fa-f-])"


class MacAddressModel(BaseModel):
    mac_address: MacAddress

    @field_validator("mac_address")
    def normalize_format(cls, v):
        parts = str(v).replace(":", "").replace("-", "")
        return ":".join(parts[i : i + 2].upper() for i in range(0, 12, 2))


def is_mac_address(mac_address: str) -> bool:
    """Check if a string is a valid MAC address."""
    try:
        MacAddressModel(mac_address=mac_address)  # type: ignore
        return True
    except ValidationError:
        return False


def extract_mac_addresses(text: str) -> list[str]:
    """Extract MAC addresses from a string.

    Examples: 00:11:22:33:44:55, 00-11-22-33-44-55
    """
    unique_macs = set()
    for mac in re.findall(MAC_REGEX, text):
        try:
            validated_mac = MacAddressModel(mac_address=mac).mac_address
            unique_macs.add(validated_mac)
        except ValidationError:
            pass
    return list(unique_macs)
