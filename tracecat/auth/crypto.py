import secrets

from tracecat import base62


def generate_token(prefix: str = "", length: int = 32) -> str:
    """Generate a secure token with an optional prefix.

    Args:
        prefix: String prefix for the token (e.g., "sk_", "api_")
        length: Number of random bytes to generate (default 32 bytes = 256 bits)

    Returns:
        Token string with the prefix and base62-encoded random data
    """
    # Generate cryptographically secure random bytes
    num = secrets.randbits(length * 8)
    base62_encoded = base62.b62encode(num)
    return f"{prefix}{base62_encoded}"
