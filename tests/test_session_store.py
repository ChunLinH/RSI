from rsi_monitor.monitoring.session_store import MonitorDefinition, MonitorSessionStore


def test_monitor_session_store_round_trips_monitor_list(tmp_path) -> None:
    store = MonitorSessionStore(tmp_path / "config" / "monitor_sessions.json")
    definitions = [
        MonitorDefinition("159952", "1min", 30, 70),
        MonitorDefinition("600000", "5min", 25, 75),
    ]

    store.save(definitions)

    assert store.load() == definitions


def test_monitor_session_store_ignores_invalid_saved_entries(tmp_path) -> None:
    path = tmp_path / "monitor_sessions.json"
    path.write_text(
        '[{"symbol": "159952", "period": "1min", "buy_threshold": 30, "sell_threshold": 70},'
        '{"symbol": "not-a-code", "period": "1min", "buy_threshold": 30, "sell_threshold": 70}]',
        encoding="utf-8",
    )

    definitions = MonitorSessionStore(path).load()

    assert definitions == [MonitorDefinition("159952", "1min", 30, 70)]
