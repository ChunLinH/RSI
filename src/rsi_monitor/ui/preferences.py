from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path


@dataclass(frozen=True)
class UiPreferences:
    chart_auto_hide: bool
    chart_indicator: str
    show_call_auction: bool = True
    quote_panel_collapsed: bool = False
    overlay_sort_mode: str = "rsi"


class UiPreferencesStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._logger = logging.getLogger(__name__)

    def load(self, defaults: UiPreferences) -> UiPreferences:
        if not self._path.exists():
            return defaults
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            indicator = str(payload.get("chart_indicator", defaults.chart_indicator)).lower()
            if indicator not in {"rsi", "macd", "kdj"}:
                indicator = defaults.chart_indicator
            overlay_sort_mode = str(
                payload.get("overlay_sort_mode", defaults.overlay_sort_mode)
            ).lower()
            if overlay_sort_mode not in {"rsi", "change_percent"}:
                overlay_sort_mode = defaults.overlay_sort_mode
            return UiPreferences(
                chart_auto_hide=bool(
                    payload.get("chart_auto_hide", defaults.chart_auto_hide)
                ),
                chart_indicator=indicator,
                show_call_auction=bool(
                    payload.get("show_call_auction", defaults.show_call_auction)
                ),
                quote_panel_collapsed=bool(
                    payload.get(
                        "quote_panel_collapsed", defaults.quote_panel_collapsed
                    )
                ),
                overlay_sort_mode=overlay_sort_mode,
            )
        except (OSError, json.JSONDecodeError, TypeError) as error:
            self._logger.warning("无法读取界面设置 %s：%s", self._path, error)
            return defaults

    def save(self, preferences: UiPreferences) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self._path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(asdict(preferences), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(self._path)
        except OSError as error:
            self._logger.warning("无法保存界面设置 %s：%s", self._path, error)
