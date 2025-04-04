def to_camel_case(s: str) -> str:
    """Convert snake_case to camelCase."""
    words = s.split("_")
    if not words:
        return ""
    return words[0] + "".join(word.capitalize() for word in words[1:])
