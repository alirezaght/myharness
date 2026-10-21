"""Compatibility import for the shared installer/verification process lifecycle."""

import sys
from al.installer import owned_process

sys.modules[__name__] = owned_process
