from rsi_monitor.signals.evaluator import SignalCondition, SignalDetector


def test_notifies_only_when_entering_same_signal_condition() -> None:
    detector = SignalDetector(buy_threshold=30, sell_threshold=70)

    events = [
        detector.evaluate(25),
        detector.evaluate(20),
        detector.evaluate(50),
        detector.evaluate(28),
        detector.evaluate(75),
        detector.evaluate(80),
    ]

    assert [event.condition for event in events] == [
        SignalCondition.BUY,
        SignalCondition.BUY,
        SignalCondition.NONE,
        SignalCondition.BUY,
        SignalCondition.SELL,
        SignalCondition.SELL,
    ]
    assert [event.should_notify for event in events] == [True, False, False, True, True, False]


def test_buy_signal_requires_returning_above_threshold_before_another_notification() -> None:
    detector = SignalDetector(buy_threshold=30, sell_threshold=70)

    events = [detector.evaluate(rsi) for rsi in [35, 29.8, 29.5, 28.5, 31, 29.9]]

    assert [event.should_notify for event in events] == [False, True, False, False, False, True]


def test_sell_signal_requires_returning_below_threshold_before_another_notification() -> None:
    detector = SignalDetector(buy_threshold=30, sell_threshold=70)

    events = [detector.evaluate(rsi) for rsi in [65, 70.1, 72, 75, 69, 70]]

    assert [event.should_notify for event in events] == [False, True, False, False, False, True]
