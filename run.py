"""Run the combined hub + league sites (see wsgi.application)."""
import os

from werkzeug.serving import run_simple

from wsgi import application


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    # Keep debugger + auto-reload by default for local dev; can disable with FLASK_USE_RELOADER=0.
    use_reloader = os.environ.get("FLASK_USE_RELOADER", "1") == "1"
    port = int(os.environ.get("PORT", "5000"))
    dev_https = os.environ.get("FLASK_DEV_HTTPS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "adhoc",
    )
    kwargs = {
        "hostname": "0.0.0.0",
        "port": port,
        "application": application,
        "use_debugger": debug,
        "use_reloader": debug and use_reloader,
    }
    if dev_https:
        # Self-signed cert: browser will warn once; then image clipboard works on LAN IPs.
        kwargs["ssl_context"] = "adhoc"
    run_simple(**kwargs)
