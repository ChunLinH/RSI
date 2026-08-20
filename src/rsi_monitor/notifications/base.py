from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationMessage:
    title: str
    content: str


@dataclass(frozen=True)
class NotificationResult:
    sent: bool
    status: str


class NotificationProvider(ABC):
    """通知渠道接口；监控逻辑不依赖具体通知服务。"""

    @abstractmethod
    def send(self, message: NotificationMessage) -> NotificationResult:
        """发送一条信号通知。"""


# 兼容前两阶段使用的名称。
NotificationService = NotificationProvider
