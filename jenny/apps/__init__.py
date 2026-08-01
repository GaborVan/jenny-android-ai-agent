"""Jenny Apps: workspace app folders with typed actions (storage/http).

See ``.agent/jenny-apps.md`` for the design and the ``app-creator`` skill for
the manifest contract.
"""

from jenny.apps.executor import AppActionError, execute_action
from jenny.apps.manifest import LoadedApp, find_app, load_app, scan_apps

__all__ = [
    "AppActionError",
    "LoadedApp",
    "execute_action",
    "find_app",
    "load_app",
    "scan_apps",
]
