# -*- coding: utf-8 -*-
"""
FaceFinder API - moteur HTTP local qui tourne SUR L'HOTE WINDOWS.

Architecture v4 :
  Telegram -> n8n -> POST /requests  (demande mise en file, statut "pending")
  GUI FaceFinder (localhost:8788/dashboard) -> file d'attente, lancer/annuler,
     resultats avec suppression a l'unite, validation d'envoi
  Validation -> POST /webhook/validate vers n8n -> envoi Telegram

Endpoints:
  GET  /health
  POST /sync                    - sync Google Drive seulement
  POST /scan-sync               - sync + scan immediat (mode direct, sans file)
  POST /scan                    - scan seulement
  POST /requests                - creer une demande {image_b64} -> {id, status:"pending"}
  GET  /requests                - liste des demandes
  GET  /requests/{id}           - detail d'une demande (+resultats si scannee)
  POST /requests/{id}/scan      - lancer le scan de la demande (sync Drive + scan)
  POST /requests/{id}/cancel    - annuler une demande
  DELETE /requests/{id}/matches/{index} - retirer une photo des resultats
  POST /requests/{id}/validate  - valider l'envoi -> notifie n8n (webhook)
  GET  /photo?path=...          - sert une image locale (pour la GUI)
  GET  /request_photo?id=...&i=N - sert la photo de reference d'une demande
  GET  /dashboard               - la page de validation (HTML)

Lancement :
  C:/Users/DELL/face-finder-venv/Scripts/python.exe facefinder_api.py
"""
import base64
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = int(os.environ.get("FACEFINDER_API_PORT", "8787"))
DATA_DIR = os.environ.get("FACEFINDER_DATA", r"C:\Users\DELL\face-finder-data")
REQ_DIR = os.path.join(DATA_DIR, "requests")
N8N_VALIDATE_URL = os.environ.get("FACEFINDER_N8N_WEBHOOK", "http://localhost:5678/webhook/facefinder-send")

os.makedirs(REQ_DIR, exist_ok=True)

_engine = None
_lock = threading.Lock()


# ---------------------------------------------------------------- moteur

def get_engine():
    """Charge InsightFace une seule fois (buffalo_l, detection+recognition uniquement)."""
    global _engine
    if _engine is None:
        import numpy as np  # noqa: F401
        from insightface.app import FaceAnalysis

        models_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
        if not os.path.isdir(models_root):
            models_root = os.path.expanduser("~/.insightface/models")
        _engine = FaceAnalysis(
            name="buffalo_l",
            root=models_root,
            allowed_modules=["detection", "recognition"],
            providers=["CPUExecutionProvider"],
        )
        _engine.prepare(ctx_id=0, det_size=(640, 640))
    return _engine


def sync_gdrive():
    """Sync le dossier Google Drive vers le dossier local via rclone (existe sur l'hote)."""
    import subprocess

    remote = os.environ.get("FACEFINDER_GDRIVE_REMOTE", "gdrive:Photos test")
    local = os.environ.get("FACEFINDER_SCANDIR", os.path.join(DATA_DIR, "scandrive"))
    rclone = os.environ.get("FACEFINDER_RCLONE", r"C:\Users\DELL\bin\rclone.exe")
    t0 = time.time()
    r = subprocess.run(
        [rclone, "sync", remote, local, "--fast-list"],
        capture_output=True, text=True, timeout=1800,
    )
    if r.returncode != 0:
        return {"error": "rclone failed", "stderr": r.stderr[-500:], "returncode": r.returncode}
    n_files = sum(len(files) for _root, _dirs, files in os.walk(local))
    return {"synced": True, "remote": remote, "local": local, "files": n_files, "elapsed_s": round(time.time() - t0, 1)}


def embed_image(path):
    """Retourne l'embedding 512d du plus grand visage de l'image, ou None."""
    import cv2

    img = cv2.imread(path)
    if img is None:
        return None
    faces = get_engine().get(img)
    if not faces:
        return None
    return max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])).normed_embedding


def scan(ref_path, dir_path, threshold=0.45):
    import numpy as np

    t0 = time.time()
    ref_emb = embed_image(ref_path)
    if ref_emb is None:
        return {"error": "no face in reference image", "ref": ref_path, "matches": [], "count": 0}

    ref_vec = np.asarray(ref_emb, dtype=np.float32)
    matches = []
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic"}
    for root, _dirs, files in os.walk(dir_path):
        for name in files:
            if os.path.splitext(name)[1].lower() not in exts:
                continue
            fpath = os.path.join(root, name)
            try:
                emb = embed_image(fpath)
            except Exception as exc:  # image corrompue etc.
                print(f"[skip] {fpath}: {exc}", file=sys.stderr)
                continue
            if emb is None:
                continue
            score = float(np.dot(ref_vec, np.asarray(emb, dtype=np.float32)))
            if score >= threshold:
                matches.append({"path": fpath, "score": round(score, 4)})
    matches.sort(key=lambda m: m["score"], reverse=True)
    return {"matches": matches, "count": len(matches), "elapsed_s": round(time.time() - t0, 1)}


# ---------------------------------------------------------------- file de demandes

def _req_path(rid):
    return os.path.join(REQ_DIR, f"{rid}.json")


def load_request(rid):
    with _lock:
        with open(_req_path(rid), encoding="utf-8") as f:
            return json.load(f)


def save_request(req):
    with _lock:
        with open(_req_path(req["id"]), "w", encoding="utf-8") as f:
            json.dump(req, f, ensure_ascii=False, indent=1)


def list_requests():
    out = []
    for name in os.listdir(REQ_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(REQ_DIR, name), encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception:
            pass
    out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return out


def run_scan_request(rid):
    """Scan en arriere-plan : met a jour le statut de la demande."""
    req = load_request(rid)
    try:
        req["status"] = "syncing"
        save_request(req)
        sync_info = sync_gdrive()
        if sync_info.get("error"):
            req["status"] = "error"
            req["error"] = {"step": "sync", **sync_info}
            save_request(req)
            return

        req["status"] = "scanning"
        req["sync"] = sync_info
        save_request(req)
        result = scan(req["ref_path"], sync_info["local"], req.get("threshold", 0.45))
        req["status"] = "done" if not result.get("error") else "error"
        req["result"] = result
        save_request(req)
    except Exception as exc:
        req["status"] = "error"
        req["error"] = {"step": "scan", "error": str(exc)}
        save_request(req)


def notify_n8n(req, removed):
    """Appelle le webhook n8n pour l'envoi Telegram des photos validees."""
    import urllib.request

    matches = [m for i, m in enumerate(req["result"].get("matches", [])) if i not in removed]
    payload = {
        "request_id": req["id"],
        "chat_id": req.get("chat_id"),
        "count": len(matches),
        "paths": [m["path"] for m in matches],
        "scores": [m["score"] for m in matches],
    }
    r = urllib.request.Request(
        N8N_VALIDATE_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r, timeout=300) as resp:
        return {"n8n_status": resp.status, "sent": len(matches)}


# ---------------------------------------------------------------- dashboard HTML

DASHBOARD_HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<title>FaceFinder - Validation</title>
<style>
:root{--fg:#e8e8e8;--muted:#9a9a9a;--bg:#16181d;--card:#1f2229;--accent:#4f8cff;--border:#2c303a;--ok:#3fb96f;--danger:#e5484d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,Segoe UI,sans-serif}
header{padding:14px 22px;border-bottom:1px solid var(--border);display:flex;gap:14px;align-items:baseline}
header h1{font-size:17px;margin:0}
header .sub{color:var(--muted);font-size:12px}
main{padding:22px;max-width:1100px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:14px}
.req-head{display:flex;align-items:center;gap:12px}
.req-head img{width:56px;height:56px;object-fit:cover;border-radius:8px;border:1px solid var(--border)}
.badge{font-size:11px;padding:2px 9px;border-radius:20px;border:1px solid var(--border);color:var(--muted)}
.badge.pending{color:#e8b34b;border-color:#e8b34b}.badge.syncing,.badge.scanning{color:var(--accent);border-color:var(--accent)}
.badge.done{color:var(--ok);border-color:var(--ok)}.badge.error{color:var(--danger);border-color:var(--danger)}
.badge.cancelled,.badge.sent{color:var(--muted)}
button{cursor:pointer;border:1px solid var(--border);background:transparent;color:var(--fg);border-radius:7px;padding:7px 14px;font-size:13px}
button:hover{border-color:var(--accent);color:var(--accent)}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.primary:hover{opacity:.85;color:#fff}
button.danger:hover{border-color:var(--danger);color:var(--danger)}
button:disabled{opacity:.4;cursor:default}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-top:12px}
.ph{position:relative;border:1px solid var(--border);border-radius:8px;overflow:hidden;background:#111}
.ph img{width:100%;height:130px;object-fit:cover;display:block}
.ph .score{position:absolute;top:6px;left:6px;background:rgba(0,0,0,.7);padding:1px 7px;border-radius:10px;font-size:11px}
.ph .rm{position:absolute;top:4px;right:4px;background:rgba(0,0,0,.7);border:none;color:var(--danger);border-radius:6px;padding:2px 8px;font-weight:700}
.ph.removed img{opacity:.25}
.ph .back{position:absolute;inset:auto 0 0 0;background:rgba(0,0,0,.7);color:#fff;font-size:11px;text-align:center;padding:3px}
.muted{color:var(--muted);font-size:12px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.empty{color:var(--muted);text-align:center;padding:40px 0}
#toast{position:fixed;bottom:18px;right:18px;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:10px 16px;display:none}
</style></head><body>
<header><h1>FaceFinder</h1><span class="sub">demandes Telegram &middot; validation</span>
<span style="flex:1"></span><span class="muted" id="clock"></span></header>
<main id="app"><div class="empty">Chargement...</div></main>
<div id="toast"></div>
<script>
const api = (p, opt) => fetch(p, opt).then(r => r.json().then(j => ({ok: r.ok, j})));
const toast = m => { const t = document.getElementById('toast'); t.textContent = m; t.style.display = 'block'; setTimeout(() => t.style.display = 'none', 2500); };
const fmtStatus = s => ({pending:'en attente',syncing:'sync Drive...',scanning:'scan...',done:'resultats prêts',error:'erreur',cancelled:'annulée',sent:'envoyée'}[s] || s);

async function refresh() {
  const {j} = await api('/requests');
  const app = document.getElementById('app');
  if (!j.length) { app.innerHTML = '<div class="empty">Aucune demande. Envoie une photo au bot Telegram.</div>'; return; }
  app.innerHTML = j.map(r => {
    const canAct = ['pending'].includes(r.status);
    const canSend = r.status === 'done' && r.result && r.result.matches && r.result.matches.length;
    const removed = r.removed || [];
    let body = '';
    if (r.status === 'done' && r.result) {
      if (!r.result.matches || !r.result.matches.length) body = '<p class="muted">Aucune photo correspondante.</p>';
      else body = '<div class="grid">' + r.result.matches.map((m, i) => `
        <div class="ph ${removed.includes(i) ? 'removed' : ''}">
          <img loading="lazy" src="/photo?path=${encodeURIComponent(m.path)}">
          <span class="score">${m.score}</span>
          ${removed.includes(i)
            ? `<button class="rm" title="Rétablir" onclick="restore('${r.id}',${i})">↺</button><div class="back">supprimée</div>`
            : `<button class="rm" title="Supprimer" onclick="removeOne('${r.id}',${i})">✕</button>`}
        </div>`).join('') + '</div>';
    }
    if (r.status === 'error') body = `<p class="muted">Erreur (${(r.error||{}).step || '?'}): ${((r.error||{}).error || '').slice(0,200)}</p>`;
    return `<div class="card">
      <div class="req-head">
        <img src="/request_photo?id=${r.id}">
        <div style="flex:1">
          <div class="row"><b>Demande ${r.id.slice(0,8)}</b><span class="badge ${r.status}">${fmtStatus(r.status)}</span>
          ${r.result && r.result.count != null ? `<span class="muted">${r.result.count} correspondance(s)</span>` : ''}</div>
          <div class="muted">${new Date(r.created_at).toLocaleString('fr-FR')}</div>
        </div>
        <div class="row">
          ${canAct ? `<button class="primary" onclick="scanReq('${r.id}')">▶ Lancer l'analyse</button>
                      <button class="danger" onclick="cancelReq('${r.id}')">Annuler</button>` : ''}
          ${['syncing','scanning'].includes(r.status) ? `<button onclick="refresh()">Rafraîchir</button>` : ''}
          ${canSend ? `<button class="primary" onclick="validateReq('${r.id}')">✔ Valider l'envoi (${r.result.matches.length - removed.length})</button>` : ''}
        </div>
      </div>${body}</div>`;
  }).join('');
}

async function scanReq(id) { await api(`/requests/${id}/scan`, {method:'POST'}); toast('Analyse lancée'); refresh(); }
async function cancelReq(id) { await api(`/requests/${id}/cancel`, {method:'POST'}); refresh(); }
async function removeOne(id, i) { await api(`/requests/${id}/matches/${i}`, {method:'DELETE'}); refresh(); }
async function restore(id, i) { await api(`/requests/${id}/matches/${i}/restore`, {method:'POST'}); refresh(); }
async function validateReq(id) {
  if (!confirm('Envoyer les photos restantes via Telegram ?')) return;
  const {ok, j} = await api(`/requests/${id}/validate`, {method:'POST'});
  toast(ok ? `Envoi lancé (${j.sent} photos)` : `Erreur: ${j.error || j.n8n_status}`);
  refresh();
}
refresh();
setInterval(refresh, 5000);
document.getElementById('clock').textContent = 'auto-refresh 5s';
</script></body></html>"""


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, ctype="image/jpeg"):
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self._send(404, {"error": "file not found", "path": path})

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs

        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            self._send(200, {"status": "ok", "engine_loaded": _engine is not None})
        elif u.path == "/dashboard":
            self._send_html(DASHBOARD_HTML)
        elif u.path == "/requests":
            self._send(200, list_requests())
        elif u.path == "/requests" and False:
            pass
        elif u.path.startswith("/requests/"):
            rid = u.path.split("/")[2]
            try:
                req = load_request(rid)
            except Exception:
                self._send(404, {"error": "request not found"})
                return
            # masquer les champs lourds
            light = {k: v for k, v in req.items() if k != "image_b64"}
            self._send(200, light)
        elif u.path == "/photo":
            path = q.get("path", [""])[0]
            # securite : ne servir que depuis le dossier de donnees
            data_root = os.path.abspath(DATA_DIR)
            if not os.path.abspath(path).startswith(data_root):
                self._send(403, {"error": "path outside data dir"})
                return
            self._send_file(path)
        elif u.path == "/request_photo":
            rid = q.get("id", [""])[0]
            try:
                req = load_request(rid)
                self._send_file(req["ref_path"])
            except Exception:
                self._send(404, {"error": "not found"})
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self):
        parts = self.path.strip("/").split("/")
        # /requests/{id}/matches/{i}
        if len(parts) == 4 and parts[0] == "requests" and parts[2] == "matches":
            rid, idx = parts[1], int(parts[3])
            req = load_request(rid)
            removed = set(req.get("removed", []))
            removed.add(idx)
            req["removed"] = sorted(removed)
            save_request(req)
            self._send(200, {"removed": req["removed"]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        from urllib.parse import urlparse

        u = urlparse(self.path)
        parts = u.path.strip("/").split("/")
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}

            if u.path == "/sync":
                self._send(200, sync_gdrive())

            elif u.path == "/scan-sync":
                sync_info = sync_gdrive()
                if sync_info.get("error"):
                    self._send(500, {"step": "sync", **sync_info})
                    return
                ref = body.get("ref", os.path.join(DATA_DIR, "ref.jpg"))
                threshold = float(body.get("threshold", 0.45))
                self._send(200, {"sync": sync_info, **scan(ref, sync_info["local"], threshold)})

            elif u.path == "/scan":
                ref = body.get("ref", "")
                directory = body.get("dir", "")
                if not os.path.isfile(ref):
                    self._send(400, {"error": "ref not found", "ref": ref})
                    return
                if not os.path.isdir(directory):
                    self._send(400, {"error": "dir not found", "dir": directory})
                    return
                self._send(200, scan(ref, directory, float(body.get("threshold", 0.45))))

            elif u.path == "/requests":
                # creation d'une demande : {image_b64, chat_id?, threshold?}
                img_b64 = body.get("image_b64", "")
                if not img_b64:
                    self._send(400, {"error": "image_b64 requis"})
                    return
                rid = uuid.uuid4().hex[:12]
                ref_path = os.path.join(REQ_DIR, f"{rid}_ref.jpg")
                with open(ref_path, "wb") as f:
                    f.write(base64.b64decode(img_b64))
                req = {
                    "id": rid,
                    "status": "pending",
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "chat_id": body.get("chat_id"),
                    "source": body.get("source", "telegram"),
                    "ref_path": ref_path,
                    "threshold": float(body.get("threshold", 0.45)),
                    "removed": [],
                }
                save_request(req)
                self._send(200, {"id": rid, "status": "pending"})

            elif len(parts) == 3 and parts[0] == "requests" and parts[2] == "scan":
                rid = parts[1]
                req = load_request(rid)
                if req["status"] not in ("pending", "done", "error"):
                    self._send(409, {"error": f"scan deja en cours ({req['status']})"})
                    return
                req["status"] = "syncing"
                req["removed"] = []
                save_request(req)
                threading.Thread(target=run_scan_request, args=(rid,), daemon=True).start()
                self._send(200, {"id": rid, "status": "syncing"})

            elif len(parts) == 3 and parts[0] == "requests" and parts[2] == "cancel":
                rid = parts[1]
                req = load_request(rid)
                req["status"] = "cancelled"
                save_request(req)
                self._send(200, {"id": rid, "status": "cancelled"})

            elif len(parts) == 5 and parts[0] == "requests" and parts[2] == "matches" and parts[4] == "restore":
                rid, idx = parts[1], int(parts[3])
                req = load_request(rid)
                req["removed"] = [i for i in req.get("removed", []) if i != idx]
                save_request(req)
                self._send(200, {"removed": req["removed"]})

            elif len(parts) == 3 and parts[0] == "requests" and parts[2] == "validate":
                rid = parts[1]
                req = load_request(rid)
                if req["status"] != "done":
                    self._send(409, {"error": "demande pas en etat 'done'"})
                    return
                removed = set(req.get("removed", []))
                matches = req["result"].get("matches", [])
                if len(matches) - len([i for i in removed if i < len(matches)]) == 0:
                    self._send(400, {"error": "aucune photo restante"})
                    return
                res = notify_n8n(req, removed)
                req["status"] = "sent"
                save_request(req)
                self._send(200, {"id": rid, "status": "sent", **res})

            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:
            self._send(500, {"error": str(exc)})

    def log_message(self, fmt, *args):
        print("[api]", fmt % args)


if __name__ == "__main__":
    print(f"FaceFinder API sur http://{HOST}:{PORT}")
    print(f"  Dashboard : http://localhost:{PORT}/dashboard")
    print(f"  n8n webhook (envoi) : {N8N_VALIDATE_URL}")
    get_engine()  # pre-charge les modeles au demarrage
    print("Moteur buffalo_l charge. Pret.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
