"""统一日志配置: 控制台 + 文件(按天轮转) + 耗时工具."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FILE = LOG_DIR / "mindbridge.log"
LOG_FORMAT = "%(asctime)s | %(name)-28s | %(levelname)-5s | %(message)s"
DATE_FORMAT = "%m-%d %H:%M:%S"

LEVELS: dict[str, int] = {"CRITICAL": 50, "ERROR": 40, "WARNING": 30, "INFO": 20, "DEBUG": 10}

_initialized = False


def setup_logging(*, level: int = logging.DEBUG, console_level: int = logging.INFO) -> None:
    """初始化日志系统(幂等,多次调用安全)."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # 控制台: INFO+, 简洁
    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(console)

    # 文件: DEBUG+, 按天轮转, 保留 7 天
    file_handler = TimedRotatingFileHandler(
        str(LOG_FILE), when="midnight", interval=1, backupCount=7, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(file_handler)

    # 静音第三方库的 DEBUG 日志
    for noisy in ["httpx", "httpcore", "chromadb", "urllib3", "watchfiles", "asyncio"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root.info("日志系统初始化完成 | 文件: %s", LOG_FILE)


def get_logger(name: str) -> logging.Logger:
    """获取 logger,自动截断模块名为 28 字符以内."""
    if name.startswith("app."):
        name = name[4:]
    if len(name) > 28:
        parts = name.split(".")
        name = ".".join([parts[0][:3]] + parts[-2:]) if len(parts) > 2 else name[:28]
    return logging.getLogger(name)


@contextmanager
def timed(label: str, *, logger: logging.Logger | None = None):
    """耗时上下文管理器,退出时自动打印耗时.

    用法:
        with timed("agent_run"):
            result = harness.run(...)
        # 输出: agent_run 耗时 2.34s
    """
    log = logger or logging.getLogger("timing")
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - t0
        log.info("%-40s 耗时 %5.2fs", label, elapsed)
