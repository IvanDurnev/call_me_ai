import os

from app import create_app, start_background_services


app = create_app()


if __name__ == "__main__":
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        start_background_services(app)
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
