"""Keep inherited host logging settings out of isolated research tests."""

import os

os.environ["LOG_FORMAT"] = "%(levelname)s %(name)s %(message)s"
