# TELORA customer web interface

This interface is served by `backend.api.app` and calls its same-origin FastAPI
routes through `js/api.js`.

From the repository root:

```bash
python -m pip install -r requirements.txt
uvicorn backend.api.app:app --reload
```

Open `http://127.0.0.1:8000`. Customer selection is a prototype Demo Mode
session. It does not authenticate the person using the browser. The Admin page
is a design placeholder and is not connected to backend reporting.
