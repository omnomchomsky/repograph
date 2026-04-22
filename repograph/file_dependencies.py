from dataclasses import dataclass


@dataclass(frozen=True)
class FileDependency:
    source: str
    target: str

__all__ = ["FileDependency"]