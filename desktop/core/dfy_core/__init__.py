"""dfy-core: read-only local aggregation layer for the Dafeiyu desktop app.

Stage 0 scope: status only. No writes outside <desktop>/.runtime, no service
control, no message sending. Standard library only on purpose: this machine has
~0.27 MB/s egress, so pip installs are a liability here.
"""

__version__ = "0.1.0"
