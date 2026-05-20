import logging
import sys


class CliLogger:
    _HANDLER_MARKER = "_theo_cli_logger_handler"

    def __init__(self):
        self.logger = logging.getLogger("theo_port")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        if not any(getattr(handler, self._HANDLER_MARKER, False) for handler in self.logger.handlers):
            handler = logging.StreamHandler(sys.stderr)
            setattr(handler, self._HANDLER_MARKER, True)
            handler.setFormatter(logging.Formatter('%(message)s'))
            self.logger.addHandler(handler)

    def info(self, msg, *args):
        self.logger.info(msg % args if args else msg)

    def error(self, msg, *args):
        self.logger.error(f"[ERROR] {msg}" % args if args else f"[ERROR] {msg}")

    def warning(self, msg, *args):
        self.logger.warning(f"[WARN] {msg}" % args if args else f"[WARN] {msg}")

    def debug(self, msg, *args):
        # Mute debug by default to keep clean
        pass


cli_logger = CliLogger()
