"""Local prototype dashboard; GET update routes retained for ESP8266 compatibility."""
from threading import Lock
from flask import Flask, jsonify, render_template


def create_app():
    app = Flask(__name__)
    lock = Lock()
    # None means no measurement received, not an empty bin.
    data = {zone: {kind: None for kind in ("ge", "ca", "gl", "pe")}
            for zone in ("t1", "t2")}

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/status")
    def status():
        with lock:
            result = {zone: values.copy() for zone, values in data.items()}
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/<bin_id>/<action>/<type_id>")
    def control_bin(bin_id, action, type_id):
        if bin_id not in data:
            return "ERROR: Wrong Bin ID", 404
        if type_id not in ("ge", "ca", "gl", "pe", "al"):
            return "ERROR: Wrong Type", 400
        if action not in ("fill", "empty"):
            return "ERROR: Wrong Action", 400
        with lock:
            kinds = list(data[bin_id]) if type_id == "al" else [type_id]
            for kind in kinds:
                data[bin_id][kind] = action == "fill"
        response = app.make_response(f"OK: {bin_id} {type_id} {action.upper()}")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/reset")
    def reset():
        with lock:
            for values in data.values():
                for kind in values:
                    values[kind] = None
        return "RESET ALL TO UNKNOWN"

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
