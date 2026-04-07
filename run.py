"""Run the combined hub + league sites (see wsgi.application)."""
import os

from werkzeug.serving import run_simple

from wsgi import application


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    port = int(os.environ.get("PORT", "5000"))
    run_simple(
        "0.0.0.0",
        port,
        application,
        use_debugger=debug,
        use_reloader=debug,
    )
