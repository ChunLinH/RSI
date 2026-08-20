from types import SimpleNamespace

from rsi_monitor.app import create_monitor_session_store
from rsi_monitor.monitoring.session_store import MonitorDefinition, MonitorSessionStore


def test_monitor_session_store_uses_windows_user_data_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    config = SimpleNamespace(monitor_state_file_path="monitor_sessions.json")

    store = create_monitor_session_store(config, tmp_path / "project")
    store.save([MonitorDefinition("159952", "1min", 30, 70)])

    expected_path = tmp_path / "local-app-data" / "RSIWatch" / "monitor_sessions.json"
    assert MonitorSessionStore(expected_path).load() == [
        MonitorDefinition("159952", "1min", 30, 70)
    ]


def test_monitor_session_store_migrates_legacy_release_data_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    project_root = tmp_path / "old-release"
    legacy_path = project_root / "config" / "monitor_sessions.json"
    MonitorSessionStore(legacy_path).save([MonitorDefinition("159325", "5min", 25, 75)])
    config = SimpleNamespace(monitor_state_file_path="config/monitor_sessions.json")

    store = create_monitor_session_store(config, project_root)

    assert store.load() == [MonitorDefinition("159325", "5min", 25, 75)]
