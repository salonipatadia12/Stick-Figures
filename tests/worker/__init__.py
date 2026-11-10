import logging

_log = logging.getLogger("stick-worker")
_log.addHandler(logging.NullHandler())
_log.propagate = False
