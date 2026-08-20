from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re


@dataclass(frozen=True)
class MonitorDefinition:
    """可安全保存到本地的监控项设置。"""

    symbol: str
    period: str
    buy_threshold: float
    sell_threshold: float


class MonitorSessionStore:
    """负责监控清单的本地读写，不保存任何通知凭据。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._logger = logging.getLogger(__name__)

    def load(self) -> list[MonitorDefinition]:
        if not self._path.exists():
            return []
        try:
            raw_items = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw_items, list):
                raise ValueError("监控清单必须是列表")
        except (OSError, json.JSONDecodeError, ValueError) as error:
            self._logger.warning("无法读取监控清单 %s：%s", self._path, error)
            return []

        definitions: list[MonitorDefinition] = []
        for item in raw_items:
            try:
                definitions.append(self._definition_from_item(item))
            except (KeyError, TypeError, ValueError) as error:
                self._logger.warning("已忽略无效监控项：%s", error)
        return definitions

    def save(self, definitions: list[MonitorDefinition]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "symbol": item.symbol,
                "period": item.period,
                "buy_threshold": item.buy_threshold,
                "sell_threshold": item.sell_threshold,
            }
            for item in definitions
        ]
        temporary_path = self._path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(self._path)

    @staticmethod
    def _definition_from_item(item: object) -> MonitorDefinition:
        if not isinstance(item, dict):
            raise ValueError("监控项不是对象")
        symbol = str(item["symbol"]).strip()
        period = str(item["period"]).strip()
        buy_threshold = float(item["buy_threshold"])
        sell_threshold = float(item["sell_threshold"])
        if not re.fullmatch(r"\d{6}", symbol):
            raise ValueError("股票/ETF 代码必须是六位数字")
        if period not in {"1min", "5min", "15min"}:
            raise ValueError("监控周期无效")
        if not 0 <= buy_threshold < sell_threshold <= 100:
            raise ValueError("RSI 阈值无效")
        return MonitorDefinition(symbol, period, buy_threshold, sell_threshold)
