# -*- coding: utf-8 -*-
"""
FaceFinder API - moteur HTTP local qui tourne SUR L'HOTE WINDOWS.
n8n (conteneur Docker) l'appelle via http://host.docker.internal:8787/scan

Endpoints:
  POST /scan  {"ref": "<chemin absolu photo reference>", "dir": "<dossier a scanner>", "threshold": 0.45}
  -> {"matches": [{"path": "...", "score": 0.82}, ...], "count": N, "elapsed_s": X}

Lancement :
  C:/Users/DELL/face-finder-venv/Scripts/python.exe facefinder_api.py
  (port 8787 par defaut)
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = int(os.environ.get("FACEFINDER_API_PORT", "8787"))

_engine = None


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


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "engine_loaded": _engine is not None})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/scan":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            ref = req.get("ref", "")
            directory = req.get("dir", "")
            threshold = float(req.get("threshold", 0.45))
            if not os.path.isfile(ref):
                self._send(400, {"error": "ref not found", "ref": ref})
                return
            if not os.path.isdir(directory):
                self._send(400, {"error": "dir not found", "dir": directory})
                return
            self._send(200, scan(ref, directory, threshold))
        except Exception as exc:
            self._send(500, {"error": str(exc)})

    def log_message(self, fmt, *args):
        print("[api]", fmt % args)


if __name__ == "__main__":
    print(f"FaceFinder API sur http://{HOST}:{PORT} (POST /scan)")
    get_engine()  # pre-charge les modeles au demarrage
    print("Moteur buffalo_l charge. Pret.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
