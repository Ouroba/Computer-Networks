"""
 web admin interface for the proxy server.

Runs on a separate port (config.ADMIN_PORT) in a daemon thread and provides:
  - Dashboard with live stats (total requests, cache hit-rate, active conns).
  - Log viewer with the most recent entries.
  - Cache inspector with per-entry invalidation and full-cache clear.
  - Blacklist / whitelist editor.

Contributed by: Ourouba
"""

import os

from flask import Flask, jsonify, render_template, request, redirect, url_for

import config
import proxy_logger
from cache_manager import cache
from filter_manager import filters

app = Flask(
    __name__,
    template_folder=os.path.join(config.BASE_DIR, "templates"),
    static_folder=os.path.join(config.BASE_DIR, "static"),
)


# Pages 

@app.route("/")
def dashboard():
    return render_template("admin.html", tab="dashboard")


@app.route("/logs")
def logs():
    return render_template("admin.html", tab="logs")


@app.route("/cache")
def cache_page():
    return render_template("admin.html", tab="cache")


@app.route("/filters")
def filters_page():
    return render_template("admin.html", tab="filters")


# JSON APIs

@app.route("/api/stats")
def api_stats():
    counters = proxy_logger.get_counters()
    cache_stats = cache.get_stats()
    return jsonify({**counters, **cache_stats})


@app.route("/api/logs")
def api_logs():
    n = request.args.get("n", 100, type=int)
    return jsonify(proxy_logger.get_recent_logs(n))


@app.route("/api/cache")
def api_cache():
    return jsonify(cache.get_entries())


@app.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    cache.clear()
    return jsonify({"status": "ok"})


@app.route("/api/cache/invalidate", methods=["POST"])
def api_cache_invalidate():
    url = request.form.get("url", "")
    if url:
        cache.invalidate(url)
    return jsonify({"status": "ok"})


@app.route("/api/filters")
def api_filters():
    return jsonify(filters.get_rules())


@app.route("/api/filters/add", methods=["POST"])
def api_filters_add():
    rule_type = request.form.get("rule_type", "blacklist")
    pattern = request.form.get("pattern", "").strip()
    if pattern:
        filters.add_rule(rule_type, pattern)
    return redirect(url_for("filters_page"))


@app.route("/api/filters/remove", methods=["POST"])
def api_filters_remove():
    rule_type = request.form.get("rule_type", "blacklist")
    pattern = request.form.get("pattern", "").strip()
    if pattern:
        filters.remove_rule(rule_type, pattern)
    return redirect(url_for("filters_page"))


@app.route("/api/filters/mode", methods=["POST"])
def api_filters_mode():
    mode = request.form.get("mode", "blacklist")
    filters.set_mode(mode)
    return redirect(url_for("filters_page"))


# Launcher
def start_admin():
    app.run(
        host=config.ADMIN_HOST,
        port=config.ADMIN_PORT,
        debug=False,
        use_reloader=False,
    )
