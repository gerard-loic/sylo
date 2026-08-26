import logging
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGS_DIR = _PROJECT_ROOT / "logs"


class DailyFileHandler(logging.Handler):
    """Écrit chaque enregistrement dans `logs/<JJMMAAAA>.log`, un fichier par jour de
    calendrier. Pas de rotation à gérer : le nom se déduit de la date à l'écriture.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _LOGS_DIR.mkdir(exist_ok=True)
            path = _LOGS_DIR / f"{date.today():%d%m%Y}.log"
            with path.open("a", encoding="utf-8") as f:
                f.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)


def configure_error_logger(logger: logging.Logger) -> logging.Logger:
    """Attache un `DailyFileHandler` au logger passé, pour n'y écrire que les erreurs
    serveur (500). Appelé une fois au démarrage sur le logger applicatif.
    """
    handler = DailyFileHandler()
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    return logger
