from types import SimpleNamespace

from rsi_monitor.app import create_monitor_session_store, ensure_config_file
from rsi_monitor.monitoring.session_store import MonitorDefinition, MonitorSessionStore


def test_ensure_config_file_copies_safe_template(tmp_path) -> None:
    config_directory = tmp_path / "project" / "config"
    config_directory.mkdir(parents=True)
    template = config_directory / "settings.example.ini"
    template.write_text("[notifications]\nprovider = console\n", encoding="utf-8")

    config_path = ensure_config_file(tmp_path / "project")

    assert config_path == config_directory / "settings.ini"
    assert config_path.read_text(encoding="utf-8") == template.read_text(encoding="utf-8")


def test_ensure_config_file_preserves_existing_settings(tmp_path) -> None:
    config_directory = tmp_path / "project" / "config"
    config_directory.mkdir(parents=True)
    config_path = config_directory / "settings.ini"
    config_path.write_text("custom", encoding="utf-8")
    (config_directory / "settings.example.ini").write_text("template", encoding="utf-8")

    assert ensure_config_file(tmp_path / "project") == config_path
    assert config_path.read_text(encoding="utf-8") == "custom"


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
