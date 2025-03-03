import re

from pydantic import TypeAdapter, ValidationError
from pydantic_extra_types.mac_address import MacAddress

# MAC ADDRESS
MAC_REGEX = r"(?<![\d:A-Fa-f-])(?:[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}|[0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5})(?![\d:A-Fa-f-])"


MacAddressTypeAdapter = TypeAdapter(MacAddress)


def is_mac(mac: str) -> bool:
    """Check if a string is a valid MAC address."""
    try:
        MacAddressTypeAdapter.validate_python(mac)
        return True
    except ValidationError:
        return False


def extract_mac(text: str) -> list[str]:
    """Extract MAC addresses from a string."""

    def _normalize_mac(mac: str) -> str:
        parts = str(mac).replace(":", "").replace("-", "")
        return ":".join(parts[i : i + 2] for i in range(0, 12, 2))

    unique_macs = set()
    for mac in re.findall(MAC_REGEX, text):
        try:
            normalized_mac = _normalize_mac(mac)
            validated_mac = MacAddressTypeAdapter.validate_python(normalized_mac)
            unique_macs.add(validated_mac)
        except ValidationError:
            pass
    return list(unique_macs)
