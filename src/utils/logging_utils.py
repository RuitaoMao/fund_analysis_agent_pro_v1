"""日志工具。"""

from __future__ import annotations

import logging


def setup_logging(level: int = logging.INFO) -> None:
    """配置最基础的日志格式。"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
