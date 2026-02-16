import logging
import sys

class CliLogger:
    def __init__(self):
        self.logger = logging.getLogger("theo_port")
        self.logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
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
