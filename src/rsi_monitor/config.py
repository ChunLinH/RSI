from configparser import ConfigParser, Error as ConfigParserError
from dataclasses import dataclass
from pathlib import Path
import re


class ConfigError(ValueError):
    """配置缺失或参数无效。"""


@dataclass(frozen=True)
class AppConfig:
    symbol: str
    price_history_size: int
    refresh_interval_seconds: float
    monitor_state_file_path: str
    default_monitoring_period: str
    allowed_monitoring_periods: tuple[str, ...]
    market_data_provider: str
    market_request_timeout_seconds: float
    max_retries: int
    retry_delay_seconds: float
    history_retry_delay_seconds: float
    market_cache_directory: str
    rsi_periods: tuple[int, ...]
    signal_rsi_period: int
    buy_rsi_threshold: float
    sell_rsi_threshold: float
    notification_provider: str
    notification_enabled: bool
    serverchan_sendkey: str
    wxpusher_spt: str
    notification_request_timeout_seconds: float
    chart_auto_hide_on_focus_loss: bool
    chart_default_indicator: str
    chart_data_limit: int
    chart_refresh_interval_seconds: float
    chart_show_call_auction: bool
    chart_quote_panel_collapsed: bool
    log_level: str
    log_file_path: str


def load_config(path: Path) -> AppConfig:
    """从 INI 文件读取并校验所有运行参数。"""
    parser = ConfigParser()
    if not parser.read(path, encoding="utf-8"):
        raise ConfigError(f"无法读取配置文件：{path}")

    try:
        rsi_periods = _parse_rsi_periods(
            parser.get(
                "indicator",
                "rsi_periods",
                fallback=parser.get("indicator", "rsi_period", fallback="14"),
            )
        )
        config = AppConfig(
            symbol=parser.get("app", "symbol").strip().upper(),
            price_history_size=parser.getint("app", "price_history_size"),
            refresh_interval_seconds=parser.getfloat(
                "app", "refresh_interval_seconds", fallback=1.0
            ),
            monitor_state_file_path=parser.get(
                "app", "monitor_state_file_path", fallback="config/monitor_sessions.json"
            ).strip(),
            default_monitoring_period=parser.get("app", "default_monitoring_period").strip(),
            allowed_monitoring_periods=tuple(
                item.strip() for item in parser.get("app", "allowed_monitoring_periods").split(",")
            ),
            market_data_provider=parser.get("market_data", "provider").strip(),
            market_request_timeout_seconds=parser.getfloat("market_data", "request_timeout_seconds"),
            max_retries=parser.getint("market_data", "max_retries"),
            retry_delay_seconds=parser.getfloat("market_data", "retry_delay_seconds"),
            history_retry_delay_seconds=parser.getfloat(
                "market_data", "history_retry_delay_seconds", fallback=30
            ),
            market_cache_directory=parser.get(
                "market_data", "cache_directory", fallback="market_cache"
            ).strip(),
            rsi_periods=rsi_periods,
            signal_rsi_period=parser.getint(
                "indicator", "signal_rsi_period", fallback=rsi_periods[0]
            ),
            buy_rsi_threshold=parser.getfloat("signals", "buy_rsi_threshold"),
            sell_rsi_threshold=parser.getfloat("signals", "sell_rsi_threshold"),
            notification_provider=parser.get("notifications", "provider").strip(),
            notification_enabled=parser.getboolean("notifications", "notification_enabled"),
            serverchan_sendkey=parser.get("notifications", "serverchan_sendkey").strip(),
            wxpusher_spt=parser.get("notifications", "wxpusher_spt", fallback="").strip(),
            notification_request_timeout_seconds=parser.getfloat(
                "notifications", "request_timeout_seconds"
            ),
            chart_auto_hide_on_focus_loss=parser.getboolean(
                "chart", "auto_hide_on_focus_loss", fallback=True
            ),
            chart_default_indicator=parser.get(
                "chart", "default_indicator", fallback="rsi"
            ).strip().lower(),
            chart_data_limit=parser.getint("chart", "data_limit", fallback=240),
            chart_refresh_interval_seconds=parser.getfloat(
                "chart", "refresh_interval_seconds", fallback=15
            ),
            chart_show_call_auction=parser.getboolean(
                "chart", "show_call_auction", fallback=True
            ),
            chart_quote_panel_collapsed=parser.getboolean(
                "chart", "quote_panel_collapsed", fallback=False
            ),
            log_level=parser.get("logging", "level").strip().upper(),
            log_file_path=parser.get("logging", "file_path").strip(),
        )
    except (ConfigParserError, ValueError) as error:
        raise ConfigError(f"配置项格式错误：{error}") from error

    _validate(config)
    return config


def _validate(config: AppConfig) -> None:
    if not config.symbol:
        raise ConfigError("app.symbol 不能为空")
    if not config.allowed_monitoring_periods:
        raise ConfigError("app.allowed_monitoring_periods 不能为空")
    if config.default_monitoring_period not in config.allowed_monitoring_periods:
        raise ConfigError("app.default_monitoring_period 必须在 allowed_monitoring_periods 中")
    if any(not _is_supported_period(period) for period in config.allowed_monitoring_periods):
        raise ConfigError("仅支持 1min、5min、15min 监控周期")
    if not config.rsi_periods:
        raise ConfigError("indicator.rsi_periods 不能为空")
    if config.signal_rsi_period not in config.rsi_periods:
        raise ConfigError("indicator.signal_rsi_period 必须在 rsi_periods 中")
    if config.price_history_size < max(config.rsi_periods) + 1:
        raise ConfigError("app.price_history_size 必须至少为最大 RSI 周期加 1")
    if config.refresh_interval_seconds <= 0:
        raise ConfigError("app.refresh_interval_seconds 必须大于 0")
    if not config.monitor_state_file_path:
        raise ConfigError("app.monitor_state_file_path 不能为空")
    if not 0 <= config.buy_rsi_threshold < config.sell_rsi_threshold <= 100:
        raise ConfigError("RSI 阈值必须满足 0 <= 买入阈值 < 卖出阈值 <= 100")
    if config.market_data_provider != "tencent_china":
        raise ConfigError("当前仅支持 market_data.provider = tencent_china")
    if config.market_request_timeout_seconds <= 0 or config.max_retries < 1:
        raise ConfigError("行情请求超时必须大于 0，重试次数必须至少为 1")
    if config.retry_delay_seconds < 0:
        raise ConfigError("retry_delay_seconds 不能小于 0")
    if config.history_retry_delay_seconds <= 0:
        raise ConfigError("history_retry_delay_seconds 必须大于 0")
    if not config.market_cache_directory:
        raise ConfigError("market_data.cache_directory 不能为空")
    if config.notification_provider not in {"console", "serverchan", "wxpusher"}:
        raise ConfigError("notifications.provider 仅支持 console、serverchan 或 wxpusher")
    if (
        config.notification_provider == "serverchan"
        and config.notification_enabled
        and not config.serverchan_sendkey
    ):
        raise ConfigError("使用 serverchan 时必须填写 notifications.serverchan_sendkey")
    if (
        config.notification_provider == "wxpusher"
        and config.notification_enabled
        and not config.wxpusher_spt
    ):
        raise ConfigError("使用 wxpusher 时必须填写 notifications.wxpusher_spt")
    if config.notification_request_timeout_seconds <= 0:
        raise ConfigError("notifications.request_timeout_seconds 必须大于 0")
    if config.chart_default_indicator not in {"rsi", "macd", "kdj"}:
        raise ConfigError("chart.default_indicator 仅支持 rsi、macd 或 kdj")
    if config.chart_data_limit < 30:
        raise ConfigError("chart.data_limit 必须至少为 30")
    if config.chart_refresh_interval_seconds <= 0:
        raise ConfigError("chart.refresh_interval_seconds 必须大于 0")
    if config.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("logging.level 无效")
    if not config.log_file_path:
        raise ConfigError("logging.file_path 不能为空")


def monitoring_period_to_milliseconds(period: str) -> int:
    match = re.fullmatch(r"([0-9]+)min", period)
    if not match:
        raise ValueError(f"无效监控周期：{period}")
    return int(match.group(1)) * 60 * 1000


def _is_supported_period(period: str) -> bool:
    return period in {"1min", "5min", "15min"}


def _parse_rsi_periods(value: str) -> tuple[int, ...]:
    periods = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not periods or any(period < 1 for period in periods):
        raise ValueError("RSI 周期必须为正整数")
    if len(set(periods)) != len(periods):
        raise ValueError("RSI 周期不能重复")
    return periods
