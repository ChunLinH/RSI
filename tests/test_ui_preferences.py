from rsi_monitor.ui.preferences import UiPreferences, UiPreferencesStore


def test_ui_preferences_round_trip(tmp_path) -> None:
    store = UiPreferencesStore(tmp_path / "preferences.json")
    preferences = UiPreferences(
        chart_auto_hide=False,
        chart_indicator="kdj",
        show_call_auction=False,
        quote_panel_collapsed=True,
        overlay_sort_mode="change_percent",
    )

    store.save(preferences)

    assert store.load(UiPreferences(True, "rsi")) == preferences
