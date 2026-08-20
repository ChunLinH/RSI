import logging

from rsi_monitor.notifications.base import (
    NotificationMessage,
    NotificationProvider,
    NotificationResult,
)


class ConsoleNotificationProvider(NotificationProvider):
    """用于本地验证的日志通知渠道。"""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._logger = logging.getLogger(__name__)

    def send(self, message: NotificationMessage) -> NotificationResult:
        if not self._enabled:
            self._logger.info("通知已关闭，跳过：%s", message.title)
            return NotificationResult(sent=False, status="通知已关闭")
        self._logger.info("控制台通知已发送：%s\n%s", message.title, message.content)
        return NotificationResult(sent=True, status="已发送")


ConsoleNotificationService = ConsoleNotificationProvider
