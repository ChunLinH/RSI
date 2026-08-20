import logging
import os
from shutil import copy2
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from rsi_monitor.config import AppConfig, load_config
from rsi_monitor.indicators.rsi import RsiCalculator
from rsi_monitor.market_data.breadth import EastmoneyMarketBreadthProvider
from rsi_monitor.market_data.tencent_china import TencentChinaMarketDataProvider
from rsi_monitor.monitoring.engine import MonitorEngine
from rsi_monitor.monitoring.session_store import MonitorSessionStore
from rsi_monitor.notifications.base import NotificationProvider, NotificationResult
from rsi_monitor.notifications.console import ConsoleNotificationProvider
from rsi_monitor.notifications.formatters import create_test_notification
from rsi_monitor.notifications.serverchan import ServerChanNotifier
from rsi_monitor.notifications.wxpusher import WxPusherNotifier
from rsi_monitor.signals.evaluator import SignalDetector
from rsi_monitor.ui.main_window import MainWindow
from rsi_monitor.ui.preferences import UiPreferences, UiPreferencesStore


def create_notifier(config: AppConfig) -> NotificationProvider:
    if config.notification_provider == "serverchan":
        return ServerChanNotifier(
            sendkey=config.serverchan_sendkey,
            timeout_seconds=config.notification_request_timeout_seconds,
            enabled=config.notification_enabled,
        )
    if config.notification_provider == "wxpusher":
        return WxPusherNotifier(
            spt=config.wxpusher_spt,
            timeout_seconds=config.notification_request_timeout_seconds,
            enabled=config.notification_enabled,
        )
    return ConsoleNotificationProvider(enabled=config.notification_enabled)


def send_test_notification(config: AppConfig, symbol: str, interval: str) -> NotificationResult:
    return create_notifier(config).send(create_test_notification(symbol, interval))


def ensure_config_file(project_root: Path) -> Path:
    """首次运行时从不含密钥的模板创建本地配置。"""
    config_directory = project_root / "config"
    config_path = config_directory / "settings.ini"
    if config_path.exists():
        return config_path

    template_path = config_directory / "settings.example.ini"
    if not template_path.exists():
        raise RuntimeError(f"缺少配置模板：{template_path}")

    config_directory.mkdir(parents=True, exist_ok=True)
    copy2(template_path, config_path)
    logging.getLogger(__name__).info("已创建默认配置：%s", config_path)
    return config_path


def create_engine(
    config: AppConfig,
    symbol: str,
    monitoring_period: str,
    buy_rsi_threshold: float,
    sell_rsi_threshold: float,
    cache_directory: Path | None = None,
) -> MonitorEngine:
    provider = TencentChinaMarketDataProvider(
        symbol=symbol,
        timeout_seconds=config.market_request_timeout_seconds,
        max_retries=config.max_retries,
        retry_delay_seconds=config.retry_delay_seconds,
        interval=monitoring_period,
        cache_directory=cache_directory,
    )
    return MonitorEngine(
        provider=provider,
        rsi_calculator=RsiCalculator(config.signal_rsi_period),
        additional_rsi_calculators={
            period: RsiCalculator(period)
            for period in config.rsi_periods
            if period != config.signal_rsi_period
        },
        signal_detector=SignalDetector(buy_rsi_threshold, sell_rsi_threshold),
        notifier=create_notifier(config),
        price_history_size=config.price_history_size,
        monitoring_period=monitoring_period,
        history_retry_delay_seconds=config.history_retry_delay_seconds,
    )


def configure_logging(config: AppConfig, project_root: Path) -> None:
    log_path = project_root / config.log_file_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )


def create_monitor_session_store(config: AppConfig, project_root: Path) -> MonitorSessionStore:
    """将监控清单保存在 Windows 用户数据目录，避免更新 EXE 后丢失。"""
    configured_path = Path(config.monitor_state_file_path)
    if configured_path.is_absolute():
        state_path = configured_path
    else:
        state_path = _user_data_root(project_root) / configured_path.name

    legacy_path = project_root / configured_path
    if state_path != legacy_path and not state_path.exists() and legacy_path.exists():
        state_path.parent.mkdir(parents=True, exist_ok=True)
        copy2(legacy_path, state_path)
        logging.getLogger(__name__).info("已迁移旧监控清单到 %s", state_path)
    return MonitorSessionStore(state_path)


def create_market_cache_directory(config: AppConfig, project_root: Path) -> Path:
    configured_path = Path(config.market_cache_directory)
    return (
        configured_path
        if configured_path.is_absolute()
        else _user_data_root(project_root) / configured_path
    )


def create_ui_preferences_store(project_root: Path) -> UiPreferencesStore:
    return UiPreferencesStore(_user_data_root(project_root) / "ui_preferences.json")


def _user_data_root(project_root: Path) -> Path:
    return Path(os.environ.get("LOCALAPPDATA", project_root)) / "RSIWatch"


def main() -> None:
    project_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    config = load_config(ensure_config_file(project_root))
    configure_logging(config, project_root)
    monitor_session_store = create_monitor_session_store(config, project_root)
    market_cache_directory = create_market_cache_directory(config, project_root)
    market_breadth_provider = EastmoneyMarketBreadthProvider(
        timeout_seconds=config.market_request_timeout_seconds,
        max_retries=config.max_retries,
        retry_delay_seconds=config.retry_delay_seconds,
    )
    preferences_store = create_ui_preferences_store(project_root)
    preferences = preferences_store.load(
        UiPreferences(
            chart_auto_hide=config.chart_auto_hide_on_focus_loss,
            chart_indicator=config.chart_default_indicator,
            show_call_auction=config.chart_show_call_auction,
            quote_panel_collapsed=config.chart_quote_panel_collapsed,
        )
    )
    application = QApplication(sys.argv)
    window = MainWindow(
        engine_factory=lambda symbol, period, buy, sell: create_engine(
            config, symbol, period, buy, sell, market_cache_directory
        ),
        initial_symbol=config.symbol,
        monitoring_periods=config.allowed_monitoring_periods,
        default_monitoring_period=config.default_monitoring_period,
        default_buy_rsi_threshold=config.buy_rsi_threshold,
        default_sell_rsi_threshold=config.sell_rsi_threshold,
        rsi_periods=config.rsi_periods,
        signal_rsi_period=config.signal_rsi_period,
        test_notification_sender=lambda symbol, interval: send_test_notification(
            config, symbol, interval
        ),
        refresh_interval_seconds=config.refresh_interval_seconds,
        saved_monitors=monitor_session_store.load(),
        session_saver=monitor_session_store.save,
        chart_data_limit=config.chart_data_limit,
        initial_chart_auto_hide=preferences.chart_auto_hide,
        initial_chart_indicator=preferences.chart_indicator,
        initial_show_call_auction=preferences.show_call_auction,
        initial_quote_panel_collapsed=preferences.quote_panel_collapsed,
        preference_saver=preferences_store.save,
        chart_refresh_interval_seconds=config.chart_refresh_interval_seconds,
        market_breadth_fetcher=market_breadth_provider.fetch_all_a,
    )
    application.setQuitOnLastWindowClosed(not window.has_system_tray)
    window.show()
    sys.exit(application.exec())
