import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from rsi_monitor.notifications.base import (
    NotificationMessage,
    NotificationProvider,
    NotificationResult,
)


class WeComWebhookNotifier(NotificationProvider):
    """通过企业微信机器人 Webhook 发送文本通知。"""

    def __init__(self, webhook_url: str, timeout_seconds: float, enabled: bool) -> None:
        self._webhook_url = webhook_url
        self._timeout_seconds = timeout_seconds
        self._enabled = enabled
        self._logger = logging.getLogger(__name__)

    def send(self, message: NotificationMessage) -> NotificationResult:
        if not self._enabled:
            self._logger.info("企业微信通知已关闭，跳过：%s", message.title)
            return NotificationResult(sent=False, status="通知已关闭")
        if not self._webhook_url:
            self._logger.error("企业微信通知发送失败：未配置 Webhook URL")
            return NotificationResult(sent=False, status="发送失败：未配置 Webhook URL")

        payload = json.dumps(
            {"msgtype": "text", "text": {"content": message.content}}, ensure_ascii=False
        ).encode("utf-8")
        try:
            request = Request(
                self._webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=self._timeout_seconds) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"HTTP 状态码：{response.status}")
                response_data = json.loads(response.read().decode("utf-8"))
            if not isinstance(response_data, dict):
                raise RuntimeError("企业微信响应格式无效")
            error_code = response_data.get("errcode", 0)
            if error_code != 0:
                error_message = response_data.get("errmsg", "未知错误")
                raise RuntimeError(f"企业微信错误 {error_code}：{error_message}")
        except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError, RuntimeError) as error:
            self._logger.exception("企业微信通知发送失败：%s", error)
            return NotificationResult(sent=False, status=f"发送失败：{error}")

        self._logger.info("企业微信通知已发送：%s", message.title)
        return NotificationResult(sent=True, status="已发送")


WeComWebhookNotificationService = WeComWebhookNotifier
