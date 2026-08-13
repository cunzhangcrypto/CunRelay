"""Threads publisher — placeholder.

Threads 官方 API 当前处于 limited availability，V1 暂缓发布。
文案已照常生成，通道就绪后在此实现 publish() 即可一键启用。
"""

from sqlite3 import Row

from .base import BasePublisher, PublishResult


class ThreadsPublisher(BasePublisher):
    name = "threads"

    def publish(self, post: Row) -> PublishResult:
        return PublishResult(False, "Threads 通道未启用（V1 暂缓）")
