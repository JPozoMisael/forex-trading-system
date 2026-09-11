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

    # En Windows, la consola suele usar cp1252 y truena con los emojis de los
    # logs (✅, 💰, 📈...). Forzamos UTF-8 en la salida estándar cuando el
    # intérprete lo permite (Python 3.7+); en Docker/Linux ya es UTF-8 por
    # defecto y esto no tiene efecto.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger