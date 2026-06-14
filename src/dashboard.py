#!/usr/bin/env python3
"""Real-time OpenPiBot endpoint dashboard (stdlib only)."""
from __future__ import annotations

import base64
import http.client
import json
import os
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("OPENPIBOT_DASHBOARD_DIR", "/home/ken/openpibot-dashboard"))
CONFIG_PATH = Path(os.environ.get("OPENPIBOT_DASHBOARD_CONFIG", APP_DIR / "endpoints.json"))
POLL_INTERVAL = float(os.environ.get("OPENPIBOT_DASHBOARD_POLL_INTERVAL", "3"))
REQUEST_TIMEOUT = float(os.environ.get("OPENPIBOT_DASHBOARD_TIMEOUT", "5"))
HISTORY_LIMIT = int(os.environ.get("OPENPIBOT_DASHBOARD_HISTORY", "120"))
LOG_DIR = Path(os.environ.get("OPENPIBOT_DASHBOARD_LOG_DIR", APP_DIR / "logs"))
ACTIVITY_LOG_PATH = LOG_DIR / "activity.jsonl"
CHAT_LOG_PATH = LOG_DIR / "chat.jsonl"
LOG_MAX_BYTES = int(os.environ.get("OPENPIBOT_DASHBOARD_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
LOG_LOCK = threading.Lock()
SESSION_DIR = Path(os.environ.get("OPENPIBOT_DASHBOARD_SESSION_DIR", APP_DIR / "sessions"))
SESSION_LOCK = threading.Lock()
ACTIVE_CHAT_LOCK = threading.Lock()
ACTIVE_CHAT_CONNECTIONS: dict[str, list[Any]] = {}
ACTIVE_CHAT_CANCELLED: set[str] = set()
SEEN_ACTIVITY_IDS: set[str] = set()

# ── Auth ──
DASHBOARD_USER = os.environ.get("OPENPIBOT_DASHBOARD_USER", "")
DASHBOARD_PASS = os.environ.get("OPENPIBOT_DASHBOARD_PASS", "")
AUTH_ENABLED = bool(DASHBOARD_USER)

# ── Rate limiting (in-memory, per-IP) ──
_rate_buckets: dict[str, list[float]] = defaultdict(list)
_rate_limits: dict[str, float] = defaultdict(float)
RATE_CHAT_LIMIT = int(os.environ.get("OPENPIBOT_DASHBOARD_RATE_CHAT", "60"))   # req/min
RATE_AGENT_LIMIT = int(os.environ.get("OPENPIBOT_DASHBOARD_RATE_AGENT", "10"))  # req/min
RATE_WINDOW = 60.0

# ── Agent URL allowlist (Tailscale + private LAN only) ──
_ALLOWED_IP_RANGES: list[str] = [
    "100.64.0.0/10",   # Tailscale
    "10.0.0.0/8",      # Class A private
    "172.16.0.0/12",   # Class B private
    "192.168.0.0/16",  # Class C private
    "127.0.0.0/8",     # loopback
]

state_lock = threading.Lock()
state: dict[str, Any] = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "last_poll_at": None,
    "poll_interval": POLL_INTERVAL,
    "endpoints": [],
    "summary": {"total": 0, "online": 0, "offline": 0, "degraded": 0},
    "recent_activity": [],
}
stop_event = threading.Event()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_value(value: Any, max_len: int = 12000) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for k, v in value.items():
            if re.search(r"(api[_-]?key|token|secret|password|authorization|bearer)", str(k), re.I):
                redacted[k] = "[redacted]"
            else:
                redacted[k] = redact_value(v, max_len=max_len)
        return redacted
    if isinstance(value, list):
        return [redact_value(v, max_len=max_len) for v in value[:200]]
    if isinstance(value, str):
        text = value.replace("\x00", "")
        text = re.sub(r"Bearer\s+[A-Za-z0-9._~+\/-]+", "Bearer [redacted]", text)
        text = re.sub(r"(api[_-]?key|token|secret|password|PI_BRIDGE_API_KEY|PI_FILE_DOWNLOAD_KEY)\s*[:=]\s*\S+", r"\1=[redacted]", text, flags=re.I)
        return text[:max_len] + ("…" if len(text) > max_len else "")
    return value


def rotate_log_if_needed(path: Path) -> None:
    if not path.exists() or path.stat().st_size <= LOG_MAX_BYTES:
        return
    rotated = path.with_suffix(path.suffix + ".1")
    if rotated.exists():
        rotated.unlink()
    path.rename(rotated)


def append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_LOCK:
        rotate_log_if_needed(path)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(redact_value(entry), ensure_ascii=False, default=str) + "\n")


def tail_jsonl(path: Path, limit: int = 100) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            out.append({"raw": line})
    return out


def log_activity_items(items: list[dict[str, Any]]) -> None:
    for item in items:
        event_id = f"{item.get('endpoint_id')}|{item.get('ts')}|{item.get('type')}|{item.get('tool','')}|{item.get('outputTps','')}"
        if event_id in SEEN_ACTIVITY_IDS:
            continue
        SEEN_ACTIVITY_IDS.add(event_id)
        if len(SEEN_ACTIVITY_IDS) > 5000:
            SEEN_ACTIVITY_IDS.clear()
        append_jsonl(ACTIVITY_LOG_PATH, {"logged_at": utc_now(), **item})


def log_chat_event(entry: dict[str, Any]) -> None:
    append_jsonl(CHAT_LOG_PATH, {"logged_at": utc_now(), **entry})


def log_file_summary() -> dict[str, Any]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for path in [ACTIVITY_LOG_PATH, CHAT_LOG_PATH, ACTIVITY_LOG_PATH.with_suffix(ACTIVITY_LOG_PATH.suffix + ".1"), CHAT_LOG_PATH.with_suffix(CHAT_LOG_PATH.suffix + ".1")]:
        if path.exists():
            files.append({"name": path.name, "path": str(path), "size": path.stat().st_size, "modified": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})
    return {"log_dir": str(LOG_DIR), "max_bytes": LOG_MAX_BYTES, "files": files}



def load_config_data() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"endpoints": []}
    data = json.loads(CONFIG_PATH.read_text())
    if isinstance(data, list):
        return {"endpoints": data}
    if isinstance(data, dict):
        data.setdefault("endpoints", [])
        return data
    return {"endpoints": []}


def save_config_data(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean = {"endpoints": []}
    allowed = {"id", "name", "kind", "location", "base_url", "api_key", "container", "service", "model"}
    for ep in data.get("endpoints", []):
        if not isinstance(ep, dict):
            continue
        clean_ep = {k: v for k, v in ep.items() if k in allowed and v not in (None, "")}
        if clean_ep.get("id") and clean_ep.get("base_url"):
            clean["endpoints"].append(clean_ep)
    tmp = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
    CONFIG_PATH.chmod(0o600)


def _ip_allowed(host: str) -> bool:
    """Return True if *host* is in an allowed private-IP range."""
    import ipaddress
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False  # not an IP — reject hostnames to prevent SSRF
    for cidr in _ALLOWED_IP_RANGES:
        if addr in ipaddress.ip_network(cidr):
            return True
    return False


def normalize_agent_payload(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = existing or {}
    agent_id = re.sub(r"[^A-Za-z0-9_.-]", "-", str(payload.get("id") or existing.get("id") or "").strip())[:80]
    if not agent_id:
        raise ValueError("Agent id is required")
    base_url = str(payload.get("base_url") or existing.get("base_url") or "").strip().rstrip("/")
    if not re.match(r"^https?://", base_url):
        raise ValueError("Base URL must start with http:// or https://")
    host = base_url.split("//", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
    if not _ip_allowed(host):
        raise ValueError(f"Base URL host {host} is not in an allowed IP range (Tailscale/private LAN only)")
    api_key = str(payload.get("api_key") or "").strip() or existing.get("api_key", "")
    ep = {
        "id": agent_id,
        "name": str(payload.get("name") or existing.get("name") or agent_id).strip(),
        "kind": str(payload.get("kind") or existing.get("kind") or "OpenPiBot endpoint").strip(),
        "location": str(payload.get("location") or existing.get("location") or "").strip(),
        "base_url": base_url,
    }
    if api_key:
        ep["api_key"] = api_key
    for optional in ("container", "service", "model"):
        value = str(payload.get(optional) or existing.get(optional) or "").strip()
        if value:
            ep[optional] = value
    return ep


def public_agent_list() -> list[dict[str, Any]]:
    out = []
    for ep in load_config_data().get("endpoints", []):
        item = endpoint_public(ep)
        item["api_key_set"] = bool(ep.get("api_key"))
        out.append(item)
    return out


def load_config() -> list[dict[str, Any]]:
    data = load_config_data()
    endpoints = data.get("endpoints", [])
    for ep in endpoints:
        ep.setdefault("history", deque(maxlen=HISTORY_LIMIT))
        if not isinstance(ep.get("history"), deque):
            ep["history"] = deque(ep.get("history", []), maxlen=HISTORY_LIMIT)
        ep.setdefault("last_status", "unknown")
        ep.setdefault("last_change_at", None)
        ep.setdefault("up_count", 0)
        ep.setdefault("down_count", 0)
    return endpoints


def http_json(url: str, key: str | None, timeout: float = REQUEST_TIMEOUT) -> tuple[int, Any, float]:
    headers = {"User-Agent": "openpibot-dashboard/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, headers=headers)
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        elapsed = (time.perf_counter() - start) * 1000
        raw = resp.read(200_000).decode("utf-8", errors="replace")
        try:
            return resp.status, json.loads(raw), elapsed
        except json.JSONDecodeError:
            return resp.status, {"raw": raw[:1000]}, elapsed


def tcp_probe(base_url: str, timeout: float = 2.0) -> bool:
    try:
        # crude parse sufficient for http://ip:port
        hostport = base_url.split("//", 1)[-1].split("/", 1)[0]
        host, port_s = hostport.rsplit(":", 1)
        with socket.create_connection((host, int(port_s)), timeout=timeout):
            return True
    except Exception:
        return False


def endpoint_public(ep: dict[str, Any]) -> dict[str, Any]:
    hidden = {"api_key", "key", "authorization"}
    out = {k: v for k, v in ep.items() if k not in hidden and k != "history"}
    hist = ep.get("history", [])
    out["history"] = list(hist)[-HISTORY_LIMIT:]
    return out


def poll_one(ep: dict[str, Any]) -> dict[str, Any]:
    base = ep["base_url"].rstrip("/")
    key = ep.get("api_key")
    result: dict[str, Any] = {
        "id": ep.get("id"),
        "name": ep.get("name"),
        "base_url": base,
        "checked_at": utc_now(),
        "status": "offline",
        "health_http": None,
        "models_http": None,
        "latency_ms": None,
        "models": [],
        "tcp_open": False,
        "error": None,
    }
    try:
        result["tcp_open"] = tcp_probe(base)
        health_status, health_body, health_ms = http_json(base + "/health", key)
        models_status, models_body, models_ms = http_json(base + "/v1/models", key)
        result["health_http"] = health_status
        result["models_http"] = models_status
        result["latency_ms"] = round(max(health_ms, models_ms), 1)
        models = []
        for item in models_body.get("data", []) if isinstance(models_body, dict) else []:
            if isinstance(item, dict) and item.get("id"):
                models.append(item["id"])
        result["models"] = models
        result["workspace"] = health_body.get("workspace") if isinstance(health_body, dict) else None
        result["tools"] = health_body.get("tools") if isinstance(health_body, dict) else None
        try:
            activity_status, activity_body, _activity_ms = http_json(base + "/activity", key)
            result["activity_http"] = activity_status
            if isinstance(activity_body, dict):
                activity = activity_body.get("activity", [])
                if isinstance(activity, list):
                    result["activity"] = activity[-30:]
                    metrics = [a for a in activity if isinstance(a, dict) and a.get("type") == "completion_metrics"]
                    if metrics:
                        latest = metrics[-1]
                        result["latest_output_tps"] = latest.get("outputTps")
                        result["latest_output_tokens"] = latest.get("outputTokens")
                        result["latest_output_elapsed_ms"] = latest.get("elapsedMs")
                        result["latest_output_kind"] = latest.get("kind")
                        result["latest_output_at"] = latest.get("ts")
        except Exception as activity_error:
            result["activity_error"] = f"{type(activity_error).__name__}: {str(activity_error)[:120]}"
        if health_status == 200 and models_status == 200:
            result["status"] = "online"
        else:
            result["status"] = "degraded"
    except urllib.error.HTTPError as e:
        result["tcp_open"] = result["tcp_open"] or True
        result["status"] = "degraded" if e.code in (401, 403) else "offline"
        result["error"] = f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)[:180]}"
    return result


def poll_loop() -> None:
    endpoints = load_config()
    config_mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0
    with state_lock:
        state["endpoints"] = [endpoint_public(ep) for ep in endpoints]
        state["summary"] = {"total": len(endpoints), "online": 0, "offline": len(endpoints), "degraded": 0}
    while not stop_event.is_set():
        current_mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0
        if current_mtime != config_mtime:
            old_by_id = {ep.get("id"): ep for ep in endpoints}
            endpoints = load_config()
            for ep in endpoints:
                old = old_by_id.get(ep.get("id"), {})
                ep["history"] = old.get("history", deque(maxlen=HISTORY_LIMIT))
                ep["last_status"] = old.get("last_status", "unknown")
                ep["last_change_at"] = old.get("last_change_at")
                ep["up_count"] = old.get("up_count", 0)
                ep["down_count"] = old.get("down_count", 0)
            config_mtime = current_mtime
        updated = []
        for ep in endpoints:
            result = poll_one(ep)
            previous = ep.get("last_status", "unknown")
            current = result["status"]
            if previous != current:
                ep["last_change_at"] = result["checked_at"]
            ep["last_status"] = current
            if current == "online":
                ep["up_count"] = ep.get("up_count", 0) + 1
                ep["down_count"] = 0
            else:
                ep["down_count"] = ep.get("down_count", 0) + 1
                ep["up_count"] = 0
            sample = {"t": result["checked_at"], "s": current, "ms": result.get("latency_ms")}
            ep["history"].append(sample)
            merged = dict(ep)
            merged.update(result)
            updated.append(endpoint_public(merged))
        summary = {
            "total": len(updated),
            "online": sum(1 for e in updated if e.get("status") == "online"),
            "degraded": sum(1 for e in updated if e.get("status") == "degraded"),
            "offline": sum(1 for e in updated if e.get("status") == "offline"),
        }
        recent_activity = []
        for e in updated:
            for a in e.get("activity", []) or []:
                if isinstance(a, dict):
                    item = dict(a)
                    item["endpoint"] = e.get("name") or e.get("id")
                    item["endpoint_id"] = e.get("id")
                    recent_activity.append(item)
        recent_activity.sort(key=lambda x: x.get("ts", ""), reverse=True)
        log_activity_items(recent_activity)
        recent_activity = recent_activity[:80]
        with state_lock:
            state["last_poll_at"] = utc_now()
            state["endpoints"] = updated
            state["summary"] = summary
            state["recent_activity"] = recent_activity
        stop_event.wait(POLL_INTERVAL)


HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>OpenPiBot Endpoint Monitor</title>
<style>
:root{--bg:#07111f;--panel:#0e1b2d;--panel2:#111f34;--text:#e5eefb;--muted:#8ea3bd;--ok:#22c55e;--bad:#ef4444;--warn:#f59e0b;--accent:#38bdf8;--line:#223653}*{box-sizing:border-box}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,Arial;background:radial-gradient(circle at top left,#123458 0,#07111f 42%,#040914 100%);color:var(--text)}header{padding:26px 28px 12px}h1{margin:0;font-size:clamp(25px,4vw,42px);letter-spacing:-.03em}.sub{color:var(--muted);margin-top:7px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;padding:12px 28px}.stat,.card{background:linear-gradient(180deg,rgba(17,31,52,.94),rgba(10,22,38,.94));border:1px solid var(--line);border-radius:18px;box-shadow:0 18px 40px rgba(0,0,0,.25)}.stat{padding:17px}.label{color:var(--muted);font-size:13px;text-transform:uppercase;letter-spacing:.08em}.num{font-size:34px;font-weight:800;margin-top:5px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px;padding:16px 28px 28px}.card{padding:18px;position:relative;overflow:hidden}.card:before{content:"";position:absolute;inset:0 0 auto 0;height:4px;background:var(--accent)}.card.online:before{background:var(--ok)}.card.degraded:before{background:var(--warn)}.card.offline:before{background:var(--bad)}.top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.name{font-weight:800;font-size:20px}.host{color:var(--muted);font-size:13px;margin-top:4px;word-break:break-all}.badge{border-radius:999px;padding:7px 11px;font-weight:800;font-size:12px;text-transform:uppercase}.online .badge{background:rgba(34,197,94,.16);color:#86efac}.degraded .badge{background:rgba(245,158,11,.16);color:#fcd34d}.offline .badge{background:rgba(239,68,68,.16);color:#fca5a5}.kv{display:grid;grid-template-columns:125px 1fr;gap:7px 12px;margin-top:16px;font-size:14px}.k{color:var(--muted)}.models{display:flex;flex-wrap:wrap;gap:6px}.pill{font-size:12px;color:#dbeafe;background:#162a46;border:1px solid #27496f;border-radius:999px;padding:4px 8px}.err{margin-top:12px;color:#fecaca;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.25);padding:9px;border-radius:10px;font-size:13px}.spark{display:flex;gap:3px;align-items:end;height:34px;margin-top:14px}.bar{width:6px;border-radius:3px;background:#334155;min-height:5px}.bar.online{background:var(--ok);height:28px}.bar.degraded{background:var(--warn);height:19px}.bar.offline{background:var(--bad);height:10px} .chat{margin:0 28px 24px;padding:18px;background:linear-gradient(180deg,rgba(17,31,52,.94),rgba(10,22,38,.94));border:1px solid var(--line);border-radius:18px}.chat h2{margin:0 0 10px;font-size:22px}.chat-controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:10px}.chat-controls input{background:#07111f;color:var(--text);border:1px solid #27496f;border-radius:12px;padding:9px;font:inherit;min-width:220px}.chat-controls button{border:1px solid #27496f;border-radius:12px;background:#10233c;color:#dbeafe;font-weight:800;padding:9px 12px;cursor:pointer}.chat-row{display:grid;grid-template-columns:260px 1fr 110px 110px;gap:10px;align-items:stretch}.chat select,.chat textarea{background:#07111f;color:var(--text);border:1px solid #27496f;border-radius:12px;padding:10px;font:inherit}.chat textarea{min-height:82px;resize:vertical}.chat button{border:0;border-radius:12px;background:linear-gradient(135deg,#38bdf8,#2563eb);color:white;font-weight:900;font-size:15px;cursor:pointer}.chat button:disabled{opacity:.55;cursor:wait}.conversation{margin-top:14px;display:flex;flex-direction:column;gap:10px;max-height:420px;overflow:auto}.msg{border:1px solid #20344f;border-radius:14px;padding:10px 12px;background:rgba(2,8,23,.32)}.msg.user{border-color:#2563eb;background:rgba(37,99,235,.13)}.msg.assistant{border-color:#334155}.msg .mhead{font-weight:900;color:#bfdbfe;margin-bottom:5px;font-size:13px}.msg .mbody{white-space:pre-wrap;word-break:break-word}.chat-results{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin-top:14px}.reply{background:rgba(2,8,23,.35);border:1px solid #20344f;border-radius:14px;padding:12px}.reply .rhead{display:flex;justify-content:space-between;gap:8px;font-weight:800;margin-bottom:8px}.reply .rbody{white-space:pre-wrap;word-break:break-word;color:#dbeafe;font-size:14px}.reply.error{border-color:rgba(239,68,68,.5)} .agents{margin:0 28px 24px;padding:18px;background:linear-gradient(180deg,rgba(17,31,52,.94),rgba(10,22,38,.94));border:1px solid var(--line);border-radius:18px}.agents h2{margin:0 0 10px;font-size:22px}.agent-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.agent-form input{background:#07111f;color:var(--text);border:1px solid #27496f;border-radius:12px;padding:9px;font:inherit}.agent-buttons{display:flex;gap:8px;flex-wrap:wrap}.agent-buttons button{border:1px solid #27496f;border-radius:12px;background:#10233c;color:#dbeafe;font-weight:800;padding:9px 12px;cursor:pointer}.agent-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-top:14px}.agent-item{border:1px solid #20344f;border-radius:14px;padding:10px;background:rgba(2,8,23,.32);cursor:pointer}.agent-item:hover{border-color:#38bdf8}.agent-item .title{font-weight:900}.agent-item .meta{color:var(--muted);font-size:13px;word-break:break-all}.activity{margin:0 28px 24px;padding:18px;background:linear-gradient(180deg,rgba(17,31,52,.94),rgba(10,22,38,.94));border:1px solid var(--line);border-radius:18px}.activity h2{margin:0 0 10px;font-size:22px}.activity-list{display:flex;flex-direction:column;gap:8px;max-height:430px;overflow:auto}.act{display:grid;grid-template-columns:170px 170px 130px 1fr;gap:10px;align-items:start;padding:9px 10px;border:1px solid #20344f;border-radius:12px;background:rgba(2,8,23,.32);font-size:13px}.act .time,.act .ep{color:var(--muted)}.act .type{font-weight:800;color:#bae6fd}.act.tool_start .type{color:#fcd34d}.act.tool_end .type{color:#86efac}.act.chat_request .type{color:#93c5fd}.act.completion_metrics .type{color:#c4b5fd}.act .detail{white-space:pre-wrap;word-break:break-word;color:#dbeafe}footer{padding:0 28px 24px;color:var(--muted);font-size:13px}@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.cards{grid-template-columns:1fr}.kv{grid-template-columns:105px 1fr}.act{grid-template-columns:1fr}.activity,.chat,.agents{margin:0 16px 20px}.chat-row{grid-template-columns:1fr}.target-panel{max-width:none}.chat button{min-height:46px}}.target-panel{position:relative;min-width:180px;max-width:280px}.target-toggle{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px 14px;font-size:13px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;user-select:none}.target-toggle:hover{border-color:var(--accent)}.target-dropdown{position:absolute;top:100%;left:0;right:0;z-index:10;background:var(--panel);border:1px solid var(--line);border-radius:8px;margin-top:4px;padding:8px;max-height:260px;overflow-y:auto}.target-actions{display:flex;gap:6px;margin-bottom:6px}.target-check{display:flex;align-items:center;gap:6px;padding:4px 6px;font-size:13px;cursor:pointer;border-radius:4px;margin-bottom:2px}.target-check:hover{background:rgba(56,189,248,.06)}.target-check input[type=checkbox]{accent-color:var(--accent)}
</style>
</head>
<body>
<header><h1>OpenPiBot Endpoint Monitor</h1><div class="sub">Real-time health for Docker OpenPiBot bridges and the Raspberry Pi native OpenPiBot instance.</div></header>
<section class="grid">
  <div class="stat"><div class="label">Total</div><div class="num" id="total">-</div></div>
  <div class="stat"><div class="label">Online</div><div class="num" id="online">-</div></div>
  <div class="stat"><div class="label">Degraded</div><div class="num" id="degraded">-</div></div>
  <div class="stat"><div class="label">Offline</div><div class="num" id="offline">-</div></div>
</section>
<section class="cards" id="cards"></section>
<section class="agents"><h2>Manage Agents</h2><div class="sub" style="margin-bottom:12px">Add, test, update, or remove OpenPiBot-compatible endpoints. API keys are saved on the Pi and hidden after save.</div><div class="agent-form"><input id="agentId" placeholder="id e.g. cwserver-pibot2"><input id="agentName" placeholder="display name"><input id="agentKind" placeholder="kind"><input id="agentLocation" placeholder="location"><input id="agentBaseUrl" placeholder="base URL http://ip:port"><input id="agentApiKey" placeholder="API key (leave blank to keep existing)" type="password"><input id="agentContainer" placeholder="container/service note"><div class="agent-buttons"><button onclick="testAgent()">Test</button><button onclick="saveAgent()">Save</button><button onclick="removeAgent()">Remove</button><button onclick="clearAgentForm()">Clear</button></div></div><div id="agentStatus" class="sub"></div><div class="agent-list" id="agentList">Loading agents…</div></section>
<section class="chat"><h2>Chat / Control Console</h2><div class="sub" style="margin-bottom:12px">Send a prompt to one or more OpenPiBot agents. Check the agents you want to chat with. API keys stay on the Pi dashboard server. Context is saved per session and per agent.</div><div class="chat-controls"><input id="chatSession" placeholder="session id" /><button onclick="newSession()">New session</button><button onclick="clearDisplayedChat()">Clear window</button><label><input type="checkbox" id="clearContext"> Clear context on send</label></div><div class="chat-row"><div class="target-panel"><div class="target-toggle" onclick="toggleTargetPanel()"><span id="targetLabel">Target: All agents</span><span id="targetArrow">&#9660;</span></div><div class="target-dropdown" id="targetDropdown" style="display:none"><div class="target-actions"><button onclick="selectAllTargets()" style="font-size:11px;padding:2px 6px">All</button><button onclick="selectNoTargets()" style="font-size:11px;padding:2px 6px">None</button></div><div id="targetCheckboxes"></div></div></div><textarea id="chatMessage" placeholder="Type a message. Enter sends; Shift+Enter adds a line..."></textarea><button id="chatSend" onclick="sendChat()">Send</button><button id="chatStop" onclick="stopChat()" disabled>Stop</button></div><div class="conversation" id="conversation"></div><div class="chat-results" id="chatResults"></div></section>
<section class="activity"><h2>Command / Activity Monitor</h2><div class="sub" style="margin-bottom:12px">Shows chat requests plus tool/command start and finish events reported by each OpenPiBot endpoint. Persistent logs: <a style="color:#93c5fd" href="/logs/activity.jsonl">activity.jsonl</a> · <a style="color:#93c5fd" href="/logs/chat.jsonl">chat.jsonl</a></div><div class="activity-list" id="activity">Loading activity…</div></section>
<footer id="footer">Loading…</footer>
<script>
function esc(s){return String(s ?? '').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
function since(iso){ if(!iso) return 'never'; const d=(Date.now()-new Date(iso).getTime())/1000; if(d<60)return Math.round(d)+'s ago'; if(d<3600)return Math.round(d/60)+'m ago'; return Math.round(d/3600)+'h ago';}
function render(data){
 const s=data.summary||{}; ['total','online','degraded','offline'].forEach(id=>document.getElementById(id).textContent=s[id]??0);
 const cards=document.getElementById('cards'); cards.innerHTML='';
 for(const e of data.endpoints||[]){
  const hist=(e.history||[]).slice(-40).map(h=>`<span class="bar ${esc(h.s)}" title="${esc(h.t)} ${esc(h.s)} ${h.ms||''}ms"></span>`).join('');
  const models=(e.models||[]).map(m=>`<span class="pill">${esc(m)}</span>`).join('') || '<span class="pill">none</span>';
  const tools=(e.tools||[]).slice(0,8).map(t=>`<span class="pill">${esc(t)}</span>`).join('') || '<span class="pill">unknown</span>';
  const div=document.createElement('div'); div.className='card '+(e.status||'offline');
  div.innerHTML=`<div class="top"><div><div class="name">${esc(e.name||e.id)}</div><div class="host">${esc(e.base_url)}/v1</div></div><div class="badge">${esc(e.status)}</div></div>
  <div class="kv">
   <div class="k">Type</div><div>${esc(e.kind||'endpoint')}</div>
   <div class="k">Location</div><div>${esc(e.location||'')}</div>
   <div class="k">Latency</div><div>${e.latency_ms?esc(e.latency_ms)+' ms':'-'}</div>
   <div class="k">Output speed</div><div>${e.latest_output_tps?esc(e.latest_output_tps)+' tok/s': '-'} ${e.latest_output_tokens?`(${esc(e.latest_output_tokens)} tokens, ${esc(Math.round((e.latest_output_elapsed_ms||0)/1000))}s)` : ''}</div>
   <div class="k">HTTP</div><div>health ${esc(e.health_http||'-')} / models ${esc(e.models_http||'-')}</div>
   <div class="k">Models</div><div class="models">${models}</div>
   <div class="k">Tools</div><div class="models">${tools}</div>
   <div class="k">Last check</div><div>${since(e.checked_at)} (${esc(e.checked_at||'never')})</div>
   <div class="k">State change</div><div>${since(e.last_change_at)}</div>
  </div>${e.error?`<div class="err">${esc(e.error)}</div>`:''}<div class="spark">${hist}</div>`;
  cards.appendChild(div);
 }
 renderTargetCheckboxes(data);
 const act=document.getElementById('activity');
 const items=data.recent_activity||[];
 if(!items.length){act.innerHTML='<div class="sub">No commands/activity recorded yet. New chats and tool runs will appear here.</div>';} else {
  act.innerHTML=items.map(a=>{
    const detail = a.type==='completion_metrics' ? `output ${a.outputTokens||0} tokens in ${Math.round((a.elapsedMs||0)/1000)}s — ${a.outputTps||0} tok/s (${a.kind||'completion'})` : (a.promptPreview || (a.tool ? ('tool: '+a.tool+(a.isError?' • error':'')) : JSON.stringify(a)));
    return `<div class="act ${esc(a.type)}"><div class="time">${esc(a.ts||'')}</div><div class="ep">${esc(a.endpoint||'')}</div><div class="type">${esc(a.type||'event')}</div><div class="detail">${esc(detail)}</div></div>`;
  }).join('');
 }
 document.getElementById('footer').textContent=`Last poll: ${data.last_poll_at||'never'} • Refreshes every ${data.poll_interval||3}s • Dashboard started ${data.started_at}`;
}


function collectAgent(){return {id:document.getElementById('agentId').value.trim(),name:document.getElementById('agentName').value.trim(),kind:document.getElementById('agentKind').value.trim(),location:document.getElementById('agentLocation').value.trim(),base_url:document.getElementById('agentBaseUrl').value.trim(),api_key:document.getElementById('agentApiKey').value.trim(),container:document.getElementById('agentContainer').value.trim()};}
function clearAgentForm(){for(const id of ['agentId','agentName','agentKind','agentLocation','agentBaseUrl','agentApiKey','agentContainer']) document.getElementById(id).value=''; document.getElementById('agentStatus').textContent='';}
function fillAgent(a){document.getElementById('agentId').value=a.id||'';document.getElementById('agentName').value=a.name||'';document.getElementById('agentKind').value=a.kind||'';document.getElementById('agentLocation').value=a.location||'';document.getElementById('agentBaseUrl').value=a.base_url||'';document.getElementById('agentApiKey').value='';document.getElementById('agentContainer').value=a.container||a.service||'';document.getElementById('agentStatus').textContent='Loaded '+(a.name||a.id)+'; leave API key blank to keep existing.';}
function getSavedTargets(){try{return JSON.parse(localStorage.getItem('openpibotTargets')||'["all"]');}catch(e){return ['all'];}}
function saveTargets(arr){localStorage.setItem('openpibotTargets',JSON.stringify(arr));}
function isAllSelected(arr){return arr.length===0||arr[0]==='all';}
function toggleTargetPanel(){const dd=document.getElementById('targetDropdown');dd.style.display=dd.style.display==='none'?'block':'none';}
function selectAllTargets(){const cbs=document.getElementById('targetCheckboxes').querySelectorAll('input[type=checkbox]');for(const cb of cbs)cb.checked=true;onTargetChange();}
function selectNoTargets(){const cbs=document.getElementById('targetCheckboxes').querySelectorAll('input[type=checkbox]');for(const cb of cbs)cb.checked=false;onTargetChange();}
function onTargetChange(){const cbs=document.getElementById('targetCheckboxes').querySelectorAll('input[type=checkbox]:checked');const checked=[...cbs].map(c=>c.value);saveTargets(checked.length===0?['all']:checked);updateTargetLabel();loadSessionTranscript();}
function updateTargetLabel(){const arr=getSavedTargets();const label=document.getElementById('targetLabel');const all=isAllSelected(arr);label.textContent=all?'Target: All agents':'Target: '+arr.length+' agent'+(arr.length===1?'':'s');if(!all){const cbs=document.getElementById('targetCheckboxes').querySelectorAll('input[type=checkbox]');const names=[];for(const cb of cbs){if(arr.includes(cb.value))names.push(cb.dataset.name||cb.value);}label.textContent='Target: '+names.join(', ');}}
function getTargetIds(){const arr=getSavedTargets();if(isAllSelected(arr))return 'all';return arr;}
function getTargetsParam(){const arr=getSavedTargets();if(isAllSelected(arr))return 'all';return arr.join(',');}
function renderTargetCheckboxes(data){const div=document.getElementById('targetCheckboxes');if(!div)return;const saved=getSavedTargets();const all=isAllSelected(saved);div.innerHTML=(data.endpoints||[]).map(e=>`<label class="target-check"><input type="checkbox" value="${esc(e.id)}" data-name="${esc(e.name||e.id)}" ${all||saved.includes(e.id)?'checked':''} onchange="onTargetChange()" />${esc(e.name||e.id)}</label>`).join('')||'<span class="sub">No agents configured</span>';updateTargetLabel();}
async function loadAgents(){try{const r=await fetch('/api/agents',{cache:'no-store'}); const data=await r.json(); const list=document.getElementById('agentList'); if(!data.ok){throw new Error(data.error||'load failed');} list.innerHTML=(data.agents||[]).map(a=>`<div class="agent-item" onclick='fillAgent(${JSON.stringify(a).replace(/'/g,"&#39;")})'><div class="title">${esc(a.name||a.id)} ${a.api_key_set?'🔐':''}</div><div class="meta">${esc(a.id)} • ${esc(a.base_url||'')}</div><div class="meta">${esc(a.location||'')} ${esc(a.kind||'')}</div></div>`).join('')||'<div class="sub">No agents configured.</div>';}catch(e){document.getElementById('agentList').innerHTML='<div class="err">Agent list error: '+esc(e.message||e)+'</div>';}}
async function agentAction(action){const status=document.getElementById('agentStatus'); status.textContent=action+'...'; try{const payload=collectAgent(); payload.action=action; const r=await fetch('/api/agents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const data=await r.json(); if(!r.ok||!data.ok) throw new Error(data.error||('HTTP '+r.status)); if(action==='test'){status.textContent=`Test ${data.result.status}: health ${data.result.health_http||'-'} models ${data.result.models_http||'-'} ${data.result.models?('models '+data.result.models.join(',')):''} ${data.result.error||''}`;} else {status.textContent=data.message||'Saved'; document.getElementById('agentApiKey').value=''; await loadAgents(); load();}}catch(e){status.textContent='Error: '+(e.message||e);}}
function testAgent(){agentAction('test');}
function saveAgent(){agentAction('save');}
function removeAgent(){if(confirm('Remove this agent from the dashboard?')) agentAction('remove');}

let activeRequestId = null;
async function stopChat(){
 if(!activeRequestId) return;
 const rid=activeRequestId;
 const btn=document.getElementById('chatStop'); btn.disabled=true; btn.textContent='Stopping…';
 try{await fetch('/api/chat/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:rid})}); appendMsg('assistant','Dashboard','Stop requested for active request.');}
 catch(e){appendMsg('assistant','Dashboard error','Stop failed: '+(e.message||e));}
 finally{btn.textContent='Stop';}
}
function getSessionId(){
 let sid=localStorage.getItem('openpibotSessionId');
 if(!sid){sid='session-'+Date.now(); localStorage.setItem('openpibotSessionId',sid);}
 const input=document.getElementById('chatSession'); if(input && input.value!==sid) input.value=sid;
 return sid;
}
function setSessionId(sid){localStorage.setItem('openpibotSessionId',sid); const input=document.getElementById('chatSession'); if(input) input.value=sid;}
function newSession(){setSessionId('session-'+Date.now()); clearDisplayedChat(); document.getElementById('chatMessage').focus();}
function clearDisplayedChat(){document.getElementById('conversation').innerHTML=''; document.getElementById('chatResults').innerHTML='';}
function appendMsg(role, endpoint, content){
 const conv=document.getElementById('conversation');
 const div=document.createElement('div'); div.className='msg '+(role==='user'?'user':'assistant');
 div.innerHTML=`<div class="mhead">${esc(role==='user'?'You':(endpoint||'Agent'))}</div><div class="mbody">${esc(content||'')}</div>`;
 conv.appendChild(div); conv.scrollTop=conv.scrollHeight;
}
async function loadSessionTranscript(){
 const sid=getSessionId(); const targets=getTargetsParam();
 try{const r=await fetch('/api/session?session_id='+encodeURIComponent(sid)+'&targets='+encodeURIComponent(targets),{cache:'no-store'}); const data=await r.json();
  if(data.ok){const conv=document.getElementById('conversation'); conv.innerHTML=''; for(const m of data.transcript||[]){appendMsg(m.role, m.endpoint_id, m.content);}}
 }catch(e){}
}
async function sendChat(){
 const btn=document.getElementById('chatSend'); const stop=document.getElementById('chatStop'); const targets=getTargetIds(); const targetStr=getTargetsParam(); const box=document.getElementById('chatMessage'); const message=box.value.trim(); const out=document.getElementById('chatResults'); const session_id=getSessionId(); const clear_context=document.getElementById('clearContext').checked; const request_id='req-'+Date.now()+'-'+Math.random().toString(36).slice(2,8); activeRequestId=request_id;
 if(!message){out.innerHTML='<div class="reply error"><div class="rhead">Message required</div></div>';return;}
 box.value=''; appendMsg('user','You',message);
 btn.disabled=true; stop.disabled=false; btn.textContent='Sending…'; out.innerHTML='<div class="reply"><div class="rhead">Waiting for response...</div><div class="rbody">Target: '+esc(targetStr)+' • Session: '+esc(session_id)+'</div></div>';
 try{
  const body=JSON.stringify({targets,target:targetStr,message,session_id,clear_context,request_id});
  const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body}); const data=await r.json();
  if(!r.ok || !data.ok){throw new Error(data.error||('HTTP '+r.status));}
  out.innerHTML=(data.results||[]).map(x=>`<div class="reply ${x.status==='ok'?'':'error'}"><div class="rhead"><span>${esc(x.endpoint)}</span><span>${esc(x.status)} ${x.latency_ms?esc(x.latency_ms)+'ms':''}</span></div><div class="rbody">${esc(x.content||x.error||'')}</div></div>`).join('') || '<div class="reply error">No results</div>';
  for(const x of data.results||[]){appendMsg('assistant', x.endpoint, x.content||x.error||'');}
  document.getElementById('clearContext').checked=false;
  load();
 }catch(e){out.innerHTML='<div class="reply error"><div class="rhead">Chat failed</div><div class="rbody">'+esc(e.message||e)+'</div></div>'; appendMsg('assistant','Dashboard error',e.message||String(e));}
 finally{if(activeRequestId===request_id) activeRequestId=null; btn.disabled=false; stop.disabled=true; stop.textContent='Stop'; btn.textContent='Send'; box.focus();}
}

async function load(){try{const r=await fetch('/api/status',{cache:'no-store'}); render(await r.json());}catch(e){document.getElementById('footer').textContent='Dashboard API error: '+e;}}
document.addEventListener('DOMContentLoaded', () => {
 getSessionId();
 const sess=document.getElementById('chatSession');
 if(sess){sess.addEventListener('change',()=>{setSessionId(sess.value); loadSessionTranscript();});}
 const box=document.getElementById('chatMessage');
 if(box){
  box.addEventListener('keydown', (e) => {
   if(e.key==='Enter' && !e.shiftKey){
    e.preventDefault();
    sendChat();
   }
  });
 }
 loadSessionTranscript();
 loadAgents();
});
load(); setInterval(load,3000);
</script>
</body>
</html>'''



def safe_session_id(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = f"session-{int(time.time() * 1000)}-{secrets.token_hex(4)}"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", raw)[:80].strip(".-")
    return safe or f"session-{int(time.time() * 1000)}-{secrets.token_hex(4)}"


def session_path(session_id: str) -> Path:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return SESSION_DIR / f"{safe_session_id(session_id)}.json"


def load_session(session_id: str) -> dict[str, Any]:
    sid = safe_session_id(session_id)
    path = session_path(sid)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("session_id", sid)
                data.setdefault("created_at", utc_now())
                data.setdefault("updated_at", utc_now())
                data.setdefault("messages_by_endpoint", {})
                return data
        except Exception:
            pass
    return {"session_id": sid, "created_at": utc_now(), "updated_at": utc_now(), "messages_by_endpoint": {}}


def save_session(data: dict[str, Any]) -> None:
    sid = safe_session_id(data.get("session_id"))
    data["session_id"] = sid
    data["updated_at"] = utc_now()
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = session_path(sid)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(redact_value(data), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    path.chmod(0o600)


def endpoint_history(session: dict[str, Any], endpoint_id: str) -> list[dict[str, str]]:
    messages_by_endpoint = session.setdefault("messages_by_endpoint", {})
    history = messages_by_endpoint.setdefault(endpoint_id, [])
    if not isinstance(history, list):
        history = []
        messages_by_endpoint[endpoint_id] = history
    clean = []
    for m in history[-30:]:
        if isinstance(m, dict) and m.get("role") in {"user", "assistant", "system"} and isinstance(m.get("content"), str):
            clean.append({"role": m["role"], "content": sanitize_for_dashboard(m["content"], 8000)})
    messages_by_endpoint[endpoint_id] = clean
    return clean


def session_transcript(session: dict[str, Any], endpoint_id: str | list[str] | None = None) -> list[dict[str, Any]]:
    if endpoint_id is None:
        ids = sorted((session.get("messages_by_endpoint") or {}).keys())
    elif isinstance(endpoint_id, list):
        ids = endpoint_id
    else:
        ids = [endpoint_id]
    out = []
    for eid in ids:
        for msg in (session.get("messages_by_endpoint") or {}).get(eid, [])[-40:]:
            if isinstance(msg, dict):
                item = dict(msg)
                item["endpoint_id"] = eid
                out.append(item)
    return out


def find_private_endpoint(endpoint_id: str) -> dict[str, Any] | None:
    for ep in load_config():
        if ep.get("id") == endpoint_id:
            return ep
    return None



def register_active_connection(request_id: str, conn: Any) -> None:
    if not request_id:
        return
    with ACTIVE_CHAT_LOCK:
        ACTIVE_CHAT_CONNECTIONS.setdefault(request_id, []).append(conn)


def unregister_active_connection(request_id: str, conn: Any) -> None:
    if not request_id:
        return
    with ACTIVE_CHAT_LOCK:
        conns = ACTIVE_CHAT_CONNECTIONS.get(request_id, [])
        if conn in conns:
            conns.remove(conn)
        if not conns:
            ACTIVE_CHAT_CONNECTIONS.pop(request_id, None)
            ACTIVE_CHAT_CANCELLED.discard(request_id)


def cancel_active_request(request_id: str) -> int:
    if not request_id:
        return 0
    with ACTIVE_CHAT_LOCK:
        ACTIVE_CHAT_CANCELLED.add(request_id)
        conns = list(ACTIVE_CHAT_CONNECTIONS.get(request_id, []))
    closed = 0
    for conn in conns:
        try:
            conn.close()
            closed += 1
        except Exception:
            pass
    return closed


def is_request_cancelled(request_id: str) -> bool:
    with ACTIVE_CHAT_LOCK:
        return request_id in ACTIVE_CHAT_CANCELLED


def chat_with_endpoint(ep: dict[str, Any], messages: list[dict[str, str]], timeout: float = 180.0, request_id: str = "") -> dict[str, Any]:
    base = ep["base_url"].rstrip("/")
    key = ep.get("api_key")
    payload = {
        "model": ep.get("model") or ep.get("id") or "openpibot",
        "messages": messages,
        "stream": False,
    }
    headers = {"Content-Type": "application/json", "User-Agent": "openpibot-dashboard-chat/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    parsed = urllib.parse.urlparse(base + "/v1/chat/completions")
    conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parsed.hostname, parsed.port, timeout=timeout)
    register_active_connection(request_id, conn)
    start = time.perf_counter()
    try:
        if is_request_cancelled(request_id):
            raise RuntimeError("Request cancelled")
        conn.request("POST", parsed.path or "/v1/chat/completions", body=json.dumps(payload).encode(), headers=headers)
        resp = conn.getresponse()
        raw = resp.read(1_000_000).decode("utf-8", errors="replace")
        elapsed = round((time.perf_counter() - start) * 1000, 1)
        if is_request_cancelled(request_id):
            raise RuntimeError("Request cancelled")
        try:
            data = json.loads(raw)
        except Exception:
            data = {"raw": raw}
        if resp.status >= 400:
            return {
                "endpoint_id": ep.get("id"),
                "endpoint": ep.get("name") or ep.get("id"),
                "status": "cancelled" if is_request_cancelled(request_id) else "error",
                "http": resp.status,
                "error": sanitize_for_dashboard(f"HTTP {resp.status}: {raw[:2000]}", 2000),
                "ts": utc_now(),
            }
        content = ""
        try:
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception:
            content = raw[:2000]
        return {
            "endpoint_id": ep.get("id"),
            "endpoint": ep.get("name") or ep.get("id"),
            "status": "ok",
            "http": resp.status,
            "latency_ms": elapsed,
            "model": data.get("model") if isinstance(data, dict) else None,
            "content": sanitize_for_dashboard(content, 12000),
            "ts": utc_now(),
        }
    except Exception as e:
        cancelled = is_request_cancelled(request_id) or "cancel" in str(e).lower() or isinstance(e, (BrokenPipeError, ConnectionResetError))
        return {
            "endpoint_id": ep.get("id"),
            "endpoint": ep.get("name") or ep.get("id"),
            "status": "cancelled" if cancelled else "error",
            "error": "Request cancelled" if cancelled else sanitize_for_dashboard(f"{type(e).__name__}: {e}", 2000),
            "ts": utc_now(),
        }
    finally:
        unregister_active_connection(request_id, conn)
        try:
            conn.close()
        except Exception:
            pass


def sanitize_for_dashboard(value: Any, max_len: int = 4000) -> str:
    text = str(value or "")
    text = text.replace("\x00", "")
    # Avoid leaking obvious bearer tokens/keys if an agent echoes environment details.
    import re
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+\/-]+", "Bearer [redacted]", text)
    text = re.sub(r"(api[_-]?key|token|secret|password|PI_BRIDGE_API_KEY|PI_FILE_DOWNLOAD_KEY)\s*[:=]\s*\S+", r"\1=[redacted]", text, flags=re.I)
    return text[:max_len] + ("…" if len(text) > max_len else "")


class Handler(BaseHTTPRequestHandler):
    def version_string(self) -> str:
        return "OpenPiBot Dashboard"

    def check_auth(self) -> bool:
        if not AUTH_ENABLED:
            return True
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Basic "):
            return False
        try:
            creds = base64.b64decode(auth_header[6:]).decode("utf-8")
            user, _, passwd = creds.partition(":")
            return secrets.compare_digest(user, DASHBOARD_USER) and secrets.compare_digest(passwd, DASHBOARD_PASS)
        except Exception:
            return False

    def require_auth(self) -> bool:
        if not AUTH_ENABLED:
            return True
        if self.check_auth():
            return True
        body = json.dumps({"ok": False, "error": "Authentication required"}).encode()
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="OpenPiBot Dashboard"')
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def _check_rate(self) -> bool:
        ip = self.client_address[0]
        now = time.time()
        bucket = _rate_buckets[ip]
        bucket[:] = [t for t in bucket if now - t < RATE_WINDOW]
        is_agent = self.path.startswith("/api/agents")
        limit = RATE_AGENT_LIMIT if is_agent else RATE_CHAT_LIMIT
        if len(bucket) >= limit:
            body = json.dumps({"ok": False, "error": "Rate limit exceeded — try again shortly"}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False
        bucket.append(now)
        return True

    def _add_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "*"))

    def _send(self, payload: bytes, content_type: str, include_body: bool = True) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self._add_security_headers()
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if include_body:
            self.wfile.write(payload)

    def do_HEAD(self) -> None:
        if not self.require_auth():
            return
        if self.path.startswith("/api/status"):
            with state_lock:
                payload = json.dumps(state, default=list).encode()
            self._send(payload, "application/json", include_body=False)
            return
        if self.path in ("/", "/index.html"):
            self._send(HTML.encode(), "text/html; charset=utf-8", include_body=False)
            return
        self.send_error(404)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._add_security_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if not self.require_auth():
            return
        if self.path.startswith("/api/agents"):
            payload = json.dumps({"ok": True, "agents": public_agent_list()}, default=str).encode()
            self._send(payload, "application/json")
            return
        if self.path.startswith("/api/session"):
            import urllib.parse
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            sid = safe_session_id(qs.get("session_id", [""])[0])
            target = qs.get("target", [""])[0]
            targets_raw = qs.get("targets", [""])[0]
            # Parse comma-separated targets or single target
            if targets_raw and targets_raw != "all":
                endpoint_ids: list[str] | None = [t.strip() for t in targets_raw.split(",") if t.strip()]
            elif target and target != "all":
                endpoint_ids = [target]
            else:
                endpoint_ids = None
            session = load_session(sid)
            payload = json.dumps({"ok": True, "session_id": session.get("session_id"), "transcript": session_transcript(session, endpoint_ids)}, default=str).encode()
            self._send(payload, "application/json")
            return
        if self.path.startswith("/api/logs/summary"):
            payload = json.dumps(log_file_summary(), default=str).encode()
            self._send(payload, "application/json")
            return
        if self.path.startswith("/api/logs/recent"):
            import urllib.parse
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            kind = (qs.get("kind", ["activity"])[0] or "activity").lower()
            limit = min(500, max(1, int(qs.get("limit", ["100"])[0])))
            path = CHAT_LOG_PATH if kind == "chat" else ACTIVITY_LOG_PATH
            payload = json.dumps({"kind": kind, "entries": tail_jsonl(path, limit)}, default=str).encode()
            self._send(payload, "application/json")
            return
        if self.path.startswith("/logs/"):
            name = self.path.split("/logs/", 1)[-1].split("?", 1)[0]
            allowed = {"activity.jsonl": ACTIVITY_LOG_PATH, "chat.jsonl": CHAT_LOG_PATH, "activity.jsonl.1": ACTIVITY_LOG_PATH.with_suffix(ACTIVITY_LOG_PATH.suffix + ".1"), "chat.jsonl.1": CHAT_LOG_PATH.with_suffix(CHAT_LOG_PATH.suffix + ".1")}
            path = allowed.get(name)
            if not path or not path.exists():
                self.send_error(404)
                return
            payload = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Content-Disposition", f"attachment; filename={name}")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path.startswith("/api/status"):
            with state_lock:
                payload = json.dumps(state, default=list).encode()
            self._send(payload, "application/json")
            return
        if self.path in ("/", "/index.html"):
            self._send(HTML.encode(), "text/html; charset=utf-8")
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if not self.require_auth():
            return
        if self.path.startswith("/api/agents"):
            if not self._check_rate():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                action = str(payload.get("action") or "").lower()
                data = load_config_data()
                endpoints = data.setdefault("endpoints", [])
                existing = next((ep for ep in endpoints if ep.get("id") == payload.get("id")), None)
                if action == "test":
                    ep = normalize_agent_payload(payload, existing)
                    result = poll_one(ep)
                    body = json.dumps({"ok": True, "result": endpoint_public(result)}, default=str).encode()
                elif action == "save":
                    ep = normalize_agent_payload(payload, existing)
                    endpoints[:] = [old for old in endpoints if old.get("id") != ep.get("id")]
                    endpoints.append(ep)
                    save_config_data(data)
                    body = json.dumps({"ok": True, "message": f"Saved {ep.get('id')}", "agents": public_agent_list()}, default=str).encode()
                elif action == "remove":
                    agent_id = str(payload.get("id") or "").strip()
                    if not agent_id:
                        raise ValueError("Agent id is required")
                    before = len(endpoints)
                    endpoints[:] = [old for old in endpoints if old.get("id") != agent_id]
                    save_config_data(data)
                    body = json.dumps({"ok": True, "message": f"Removed {agent_id}" if len(endpoints) != before else f"No agent named {agent_id}", "agents": public_agent_list()}, default=str).encode()
                else:
                    raise ValueError("Unknown action")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                body = json.dumps({"ok": False, "error": sanitize_for_dashboard(f"{type(e).__name__}: {e}")}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            return
        if self.path.startswith("/api/chat/cancel"):
            if not self._check_rate():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                request_id = safe_session_id(payload.get("request_id"))
                closed = cancel_active_request(request_id)
                body = json.dumps({"ok": True, "request_id": request_id, "closed": closed}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                body = json.dumps({"ok": False, "error": sanitize_for_dashboard(f"{type(e).__name__}: {e}")}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            return
        if not self.path.startswith("/api/chat"):
            self.send_error(404)
            return
        if not self._check_rate():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            message = sanitize_for_dashboard(payload.get("message", ""), 8000).strip()
            target = str(payload.get("target", "")).strip()
            targets_raw = payload.get("targets")
            if isinstance(targets_raw, list) and len(targets_raw) > 0 and targets_raw[0] != "all":
                target_ids = [str(t).strip() for t in targets_raw if str(t).strip()]
            elif target and target != "all":
                target_ids = [target]
            else:
                target_ids = []  # empty = all agents
            if not message:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Message required"}).encode())
                return
            session_id = safe_session_id(payload.get("session_id"))
            request_id = safe_session_id(payload.get("request_id") or f"chatreq-{int(time.time() * 1000)}")
            clear_context = bool(payload.get("clear_context"))
            endpoints = load_config()
            if target_ids:
                endpoints = [ep for ep in endpoints if ep.get("id") in target_ids]
            if not endpoints:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "No matching endpoint"}).encode())
                return
            endpoints = endpoints[:12]
            chat_id = f"chat-{int(time.time() * 1000)}"
            with SESSION_LOCK:
                session = load_session(session_id)
                if clear_context:
                    session["messages_by_endpoint"] = {}
                prepared: dict[str, list[dict[str, str]]] = {}
                for ep in endpoints:
                    eid = str(ep.get("id"))
                    hist = endpoint_history(session, eid)
                    hist.append({"role": "user", "content": message})
                    prepared[eid] = list(hist)
                save_session(session)
            log_chat_event({"chat_id": chat_id, "session_id": session_id, "type": "request", "target": target or "all", "targets": target_ids, "message": message, "endpoint_ids": [ep.get("id") for ep in endpoints]})
            results: list[dict[str, Any]] = []
            threads: list[threading.Thread] = []
            results_lock = threading.Lock()
            def worker(ep: dict[str, Any]) -> None:
                eid = str(ep.get("id"))
                result = chat_with_endpoint(ep, prepared[eid], request_id=request_id)
                with results_lock:
                    results.append(result)
            for ep in endpoints:
                t = threading.Thread(target=worker, args=(ep,), daemon=True)
                threads.append(t)
                t.start()
            for t in threads:
                t.join(timeout=190)
            results.sort(key=lambda r: r.get("endpoint", ""))
            with SESSION_LOCK:
                session = load_session(session_id)
                for ep in endpoints:
                    eid = str(ep.get("id"))
                    hist = endpoint_history(session, eid)
                    # Ensure the latest user message exists even if session was reloaded/truncated.
                    if not hist or hist[-1].get("role") != "user" or hist[-1].get("content") != message:
                        hist.append({"role": "user", "content": message})
                    match = next((r for r in results if r.get("endpoint_id") == eid), None)
                    if match and match.get("status") == "ok":
                        hist.append({"role": "assistant", "content": sanitize_for_dashboard(match.get("content", ""), 12000), "ts": match.get("ts")})
                    elif match:
                        hist.append({"role": "assistant", "content": f"[error] {sanitize_for_dashboard(match.get('error', 'unknown error'), 2000)}", "ts": match.get("ts")})
                    session.setdefault("messages_by_endpoint", {})[eid] = hist[-30:]
                save_session(session)
                transcript = session_transcript(session, target_ids[0] if len(target_ids) == 1 else (target_ids if target_ids else None))
            log_chat_event({"chat_id": chat_id, "session_id": session_id, "type": "response", "target": target or "all", "targets": target_ids, "count": len(results), "results": results})
            body = json.dumps({"ok": True, "session_id": session_id, "request_id": request_id, "target": target or "all", "targets": target_ids, "count": len(results), "results": results, "transcript": transcript}, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = json.dumps({"ok": False, "error": sanitize_for_dashboard(f"{type(e).__name__}: {e}", 2000)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def main() -> None:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"Missing config: {CONFIG_PATH}")
    host = os.environ.get("OPENPIBOT_DASHBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("OPENPIBOT_DASHBOARD_PORT", "8766"))
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"OpenPiBot dashboard listening on http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
