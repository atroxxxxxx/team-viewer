"""Журнал desktop-приложений; HTTP-пароли и тела запросов сюда не пишутся."""
import logging
from logging.handlers import RotatingFileHandler
from support_protocol.settings import config_dir


def configure(role):
    logger = logging.getLogger("support_protocol")
    logger.setLevel(logging.INFO)
    application = logging.getLogger(f"{role}_app")
    application.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        directory = config_dir() / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(directory / f"{role}.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        logging.basicConfig(level=logging.INFO)
        logger.exception("Cannot open desktop log; using console")
        return
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    application.addHandler(handler)
