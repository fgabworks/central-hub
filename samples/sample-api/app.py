"""Tiny local sample API for Central Hub Phase 4 demos."""

from __future__ import annotations

from flask import Flask, jsonify

app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "sample-api", "status": "healthy"})


@app.get("/api/status")
def status():
    return jsonify({"ok": True, "version": "0.1.0", "capabilities": ["health", "status"]})


if __name__ == "__main__":
    # Demo only — binds localhost.
    app.run(host="127.0.0.1", port=9099, debug=False)
