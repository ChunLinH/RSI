import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from rsi_monitor.notifications.base import (
    NotificationMessage,
    NotificationProvider,
    NotificationResult,
)


class WxPusherNotifier(NotificationProvider):
    """通过 WxPusher 极简推送 SPT 发送个人通知。"""

    _API_URL = "https://wxpusher.zjiecode.com/api/send/message/simple-push"

    def __init__(self, spt: str, timeout_seconds: float, enabled: bool) -> None:
        self._spt = spt.strip()
        self._timeout_seconds = timeout_seconds
        self._enabled = enabled
        self._logger = logging.getLogger(__name__)

    def send(self, message: NotificationMessage) -> NotificationResult:
        if not self._enabled:
            self._logger.info("WxPusher 通知已关闭，跳过：%s", message.title)
            return NotificationResult(sent=False, status="通知已关闭")
        if not self._spt or self._spt in {"你的SPT", "SPT_xxx", "your_spt"}:
            self._logger.error("WxPusher 通知发送失败：未配置 SPT")
            return NotificationResult(sent=False, status="发送失败：未配置 WxPusher SPT")

        payload = json.dumps(
            {
                "content": message.content,
                "summary": message.title,
                "contentType": 1,
                "spt": self._spt,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            request = Request(
                self._API_URL,
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urlopen(request, timeout=self._timeout_seconds) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"HTTP 状态码：{response.status}")
                response_data = json.loads(response.read().decode("utf-8"))
            if not isinstance(response_data, dict):
                raise RuntimeError("WxPusher 响应格式无效")
            if response_data.get("code") != 1000:
                raise RuntimeError(str(response_data.get("msg", "WxPusher 返回未知错误")))
        except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError, RuntimeError) as error:
            self._logger.exception("WxPusher 通知发送失败：%s", error)
            return NotificationResult(sent=False, status=f"发送失败：{error}")

        self._logger.info("WxPusher 通知已发送：%s", message.title)
        return NotificationResult(sent=True, status="已发送")


# 保留一个更具体的名称，便于调用方按 SPT 方式区分其他 WxPusher 接入方式。
WxPusherSptNotifier = WxPusherNotifier
