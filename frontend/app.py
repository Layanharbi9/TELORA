"""Run the integrated TELORA FastAPI service and static customer interface.

Preferred development command: ``uvicorn backend.api.app:app --reload``.
This module is a convenience entry point for ``python -m frontend.app``.
"""

import uvicorn


if __name__ == "__main__":
    uvicorn.run("backend.api.app:app", host="127.0.0.1", port=8000, reload=True)
