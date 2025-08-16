from dataclasses import dataclass


@dataclass(frozen=True)
class GitUrl:
    """Immutable Git URL representation."""

    host: str
    org: str
    repo: str
    ref: str | None = None

    def to_url(self) -> str:
        """Convert GitUrl to string representation."""
        base = f"git+ssh://git@{self.host}/{self.org}/{self.repo}.git"
        return f"{base}@{self.ref}" if self.ref else base
