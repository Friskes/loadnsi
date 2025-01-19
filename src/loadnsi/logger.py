import logging
from typing import Any

try:
    from logging import NullHandler
except ImportError:

    class NullHandler(logging.Handler):  # type: ignore[no-redef]
        def emit(self, record: Any) -> Any:
            pass


log = logging.getLogger(__name__)
log.addHandler(NullHandler())
log.propagate = False  # Disabling the transmission of logs higher up the chain


def switch_logger(
    state: bool,
    handler: logging.Handler | None = None,
    formatter: logging.Formatter | None = None,
    level: str = 'DEBUG',
) -> None:
    if state:
        console_handler = handler or logging.StreamHandler()
        console_formatter = formatter or logging.Formatter(
            '%(filename)s:%(lineno)d | def %(funcName)s | %(message)s'
        )

        console_handler.setFormatter(console_formatter)
        log.addHandler(console_handler)
        log.setLevel(getattr(logging, level))
    else:
        log.addHandler(NullHandler)  # type: ignore[arg-type]
