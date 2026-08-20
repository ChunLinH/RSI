from pathlib import Path

import pytest

from rsi_monitor.config import ConfigError, load_config


def test_load_default_config() -> None:
    config_path = Path(__file__).parents[1] / "config" / "settings.ini"

    config = load_config(config_path)

    assert config.symbol == "SPY"
    assert config.rsi_periods == (6,)
    assert config.signal_rsi_period == 6
    assert config.buy_rsi_threshold == 30
    assert config.sell_rsi_threshold == 70
    assert config.notification_provider == "wxpusher"
    assert config.notification_enabled is True
    assert config.wxpusher_spt == "你的SPT"
    assert config.market_data_provider == "tencent_china"
    assert config.allowed_monitoring_periods == ("1min", "5min", "15min")
    assert config.refresh_interval_seconds == 1
    assert config.monitor_state_file_path == "monitor_sessions.json"
    assert config.history_retry_delay_seconds == 30
    assert config.market_cache_directory == "market_cache"
    assert config.chart_auto_hide_on_focus_loss is True
    assert config.chart_default_indicator == "rsi"
    assert config.chart_data_limit == 240
    assert config.chart_refresh_interval_seconds == 15
    assert config.chart_show_call_auction is True
    assert config.chart_quote_panel_collapsed is False


def test_rejects_invalid_signal_thresholds(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.ini"
    config_path.write_text(
        "[app]\n"
        "symbol = SPY\n"
        "price_history_size = 20\n"
        "default_monitoring_period = 1min\n"
        "allowed_monitoring_periods = 1min, 5min, 15min\n"
        "[market_data]\n"
        "provider = tencent_china\n"
        "request_timeout_seconds = 5\n"
        "max_retries = 2\n"
        "retry_delay_seconds = 0\n"
        "[indicator]\n"
        "rsi_period = 14\n"
        "[signals]\n"
        "buy_rsi_threshold = 80\n"
        "sell_rsi_threshold = 70\n"
        "[notifications]\n"
        "provider = console\n"
        "notification_enabled = false\n"
        "serverchan_sendkey =\n"
        "request_timeout_seconds = 5\n"
        "[logging]\n"
        "level = INFO\n"
        "file_path = logs/rsi_monitor.log\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="RSI 阈值"):
        load_config(config_path)
