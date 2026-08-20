import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from rsi_monitor.notifications.base import (
    NotificationMessage,
    NotificationProvider,
    NotificationResult,
)


class ServerChanNotifier(NotificationProvider):
    """通过 Server酱向个人微信发送 Markdown 文本通知。"""

    _API_BASE_URL = "https://sctapi.ftqq.com"

    def __init__(self, sendkey: str, timeout_seconds: float, enabled: bool) -> None:
        self._sendkey = sendkey
        self._timeout_seconds = timeout_seconds
        self._enabled = enabled
        self._logger = logging.getLogger(__name__)

    def send(self, message: NotificationMessage) -> NotificationResult:
        if not self._enabled:
            self._logger.info("Server酱通知已关闭，跳过：%s", message.title)
            return NotificationResult(sent=False, status="通知已关闭")
        if not self._sendkey or self._sendkey == "你的SendKey":
            self._logger.error("Server酱通知发送失败：未配置 SendKey")
            return NotificationResult(sent=False, status="发送失败：未配置 Server酱 SendKey")

        endpoint = f"{self._API_BASE_URL}/{quote(self._sendkey, safe='')}.send"
        payload = urlencode({"title": message.title, "desp": message.content}).encode("utf-8")
        try:
            request = Request(
                endpoint,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
                method="POST",
            )
            with urlopen(request, timeout=self._timeout_seconds) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"HTTP 状态码：{response.status}")
                response_data = json.loads(response.read().decode("utf-8"))
            if not isinstance(response_data, dict):
                raise RuntimeError("Server酱响应格式无效")
            if response_data.get("code") != 0:
                raise RuntimeError(str(response_data.get("message", "Server酱返回未知错误")))
        except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError, RuntimeError) as error:
            self._logger.exception("Server酱通知发送失败：%s", error)
            return NotificationResult(sent=False, status=f"发送失败：{error}")

        self._logger.info("Server酱通知已发送：%s", message.title)
        return NotificationResult(sent=True, status="已发送")
