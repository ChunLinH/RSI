import ctypes
import os
from pathlib import Path
import sys
import tempfile
import traceback


def _write_startup_error(error_text: str) -> Path | None:
    """将冻结程序的启动异常写入一个不依赖 Qt 的本地日志。"""
    if getattr(sys, "frozen", False):
        install_directory = Path(sys.executable).resolve().parent
    else:
        install_directory = Path(__file__).resolve().parents[2]

    candidates = [
        install_directory / "startup_error.log",
        Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
        / "RSIWatch"
        / "startup_error.log",
        Path(tempfile.gettempdir()) / "RSIWatch" / "startup_error.log",
    ]
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(error_text, encoding="utf-8")
            return path
        except OSError:
            continue
    return None


def _show_startup_error(error_text: str) -> None:
    log_path = _write_startup_error(error_text)
    location = f"\n\n错误日志：{log_path}" if log_path else ""
    message = f"RSIWatch 启动失败。{location}\n\n{error_text[-1800:]}"
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "RSIWatch", 0x10)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        from rsi_monitor.app import main

        main()
    except Exception:
        _show_startup_error(traceback.format_exc())
        raise
