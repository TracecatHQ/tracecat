# Base62 character set: 0-9, a-z, A-Z
_BASE62_CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Mapping of base62 characters to their corresponding integer values
_BASE62_CHAR_TO_INT = {char: i for i, char in enumerate(_BASE62_CHARS)}


def b62encode(num: int) -> str:
    """Convert a non-negative integer to a base62 string.

    Args:
        num: Non-negative integer to encode

    Returns:
        Base62 encoded string

    Raises:
        ValueError: If input is negative
    """
    if num < 0:
        raise ValueError("Number must be non-negative")

    if num == 0:
        return _BASE62_CHARS[0]

    encoded = []
    while num:
        num, remainder = divmod(num, 62)
        encoded.append(_BASE62_CHARS[remainder])

    return "".join(reversed(encoded))


def b62decode(encoded: str) -> int:
    """Convert a base62 string back to an integer.

    Args:
        encoded: Base62 encoded string

    Returns:
        Decoded integer value

    Raises:
        ValueError: If string contains invalid characters
    """
    num = 0
    for char in encoded:
        try:
            num = num * 62 + _BASE62_CHAR_TO_INT[char]
        except KeyError as e:
            raise ValueError(f"Invalid base62 character: {char}") from e
    return num
