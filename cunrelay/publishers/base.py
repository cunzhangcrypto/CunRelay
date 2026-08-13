"""Base publisher interface."""

from dataclasses import dataclass
from sqlite3 import Row


@dataclass
class PublishResult:
    success: bool
    message: str
    url: str | None = None


class BasePublisher:
    """Abstract publisher for a single platform.

    ``publish`` receives a posts-table row and returns a PublishResult.
    """

    name: str = "base"

    def publish(self, post: Row) -> PublishResult:
        raise NotImplementedError
