# Mass Spec Acquisition Tracker

Simple setup and run instructions.

## Run locally

```bash
python3 -m venv .sample_tracker
source .sample_tracker/bin/activate
pip install -r requirements.txt
python3 -m flask --app app init-db

# Local development: FLASK_DEBUG=1 allows the built-in insecure dev key.
FLASK_DEBUG=1 python3 -m flask --app app run
```

For any non-debug / shared deployment, set a real secret instead of using debug mode —
the app refuses to start otherwise:

```bash
export SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
python3 -m flask --app app run
```

## Open in browser

After the Flask server starts, open:

`http://127.0.0.1:5000`
