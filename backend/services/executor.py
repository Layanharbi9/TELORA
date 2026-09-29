"""Per-process identity for the executor running a request's Search/Design work.

Web workflows run in-process (FastAPI BackgroundTasks), so a web boot that is
no longer the current one cannot still be executing anything. This relies on
the project's single web worker; with several workers, one worker's startup
would treat another live worker's runs as abandoned.
"""

from uuid import uuid4

BOOT_ID = uuid4().hex
WEB_EXECUTOR_ID = f"web:{BOOT_ID}"
CLI_EXECUTOR_ID = f"cli:{BOOT_ID}"
