"""
Logger centralizado. Uso en cualquier servicio:
    from shared.logger import get_logger
    log = get_logger(__name__)
    log.info("mensaje")
"""
import logging
import sys

from shared.config import settings


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger  # evita handlers duplicados si se llama varias veces

    logger.setLevel(settings.log_level)

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger