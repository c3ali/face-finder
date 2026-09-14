#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Face Finder — Retrouve une personne dans un dossier de photos.
100% local, offline, aucun telechargement (modeles pre-bundles).
"""
import os, sys, io, json, threading, shutil, hashlib, datetime
from pathlib import Path

# FIX: PyInstaller --windowed met sys.stdout = None.
# insightface utilise tqdm qui ecrit sur sys.stdout -> 'NoneType' has no attribute 'write'
class _NullWriter(io.TextIOBase):
    def write(self, *a, **k): return 0
    def flush(self): pass
    def isatty(self): return False
if sys.stdout is None:
    sys.stdout = _NullWriter()
if sys.stderr is None:
    sys.stderr = _NullWriter()

import numpy as np
import tkinter as tk
import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk
from customtkinter import CTkImage

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

# ---------- Config ----------
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic", ".heif", ".tiff", ".tif"}
THRESHOLD_MIN, THRESHOLD_MAX, THRESHOLD_DEFAULT = 0.20, 0.70, 0.45

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

CARD = "#1e2130"
ACCENT = "#3b82f6"
ACCENT2 = "#10b981"
DANGER = "#ef4444"
TEXT = "#e8eaf0"
MUTED = "#8b93a7"


def resource_path(rel):
    """Chemin compatible dev et PyInstaller (sys._MEIPASS)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def models_root():
    """Dossier contenant les modeles buffalo_l (bundles ou ~/.insightface)."""
    bundled = resource_path("models")
    if os.path.isdir(os.path.join(bundled, "buffalo_l")):
        return bundled
    return os.path.join(str(Path.home()), ".insightface", "models")


# ---------- Moteur ----------
_engine = None
_engine_err = None


def get_engine():
    global _engine, _engine_err
    if _engine is not None:
        return _engine
    if _engine_err:
        raise _engine_err
    try:
        from insightface.app import FaceAnalysis
        root = models_root()
        # allowed_modules: detection+recognition uniquement -> n'exige QUE
        # det_10g.onnx + w600k_r50.onnx (les 2 seuls modèles bundlés).
        # Sans ça, insightface tente de télécharger tout le pack buffalo_l
        # (landmark, genderage...) -> 'NoneType' has no 'write' sous --windowed.
        app = FaceAnalysis(name="buffalo_l", root=root,
                           allowed_modules=["detection", "recognition"],
                           providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=(640, 640))
        _engine = app
        return app
    except Exception as e:
        _engine_err = e
        raise


def cos_sim(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def get_embedding(path, engine):
    """Retourne (embedding, bbox, nb_visages) ou (None, None, 0) si echec."""
    try:
        img = Image.open(path).convert("RGB")
        arr = np.array(img)[:, :, ::-1]  # RGB -> BGR
        faces = engine.get(arr)
        if not faces:
            return None, None, 0
        faces = sorted(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
        f = faces[0]
        emb = f.normed_embedding if hasattr(f, "normed_embedding") else f.embedding / np.linalg.norm(f.embedding)
        return emb, f.bbox, len(faces)
    except Exception:
        return None, None, 0


# ---------- Cache SQLite ----------
_cache_db = None


def get_cache():
    global _cache_db
    if _cache_db is None:
        import sqlite3
        conn = sqlite3.connect(str(Path.home() / ".facefinder_cache.db"))
        conn.execute("""CREATE TABLE IF NOT EXISTS file_cache(
            path TEXT PRIMARY KEY, mtime REAL, emb BLOB, bbox BLOB, count INTEGER, img_hash TEXT)""")
        conn.commit()
        _cache_db = conn
    return _cache_db


def cache_get(path):
    try:
        mtime = os.path.getmtime(path)
        h = hashlib.md5(str(mtime).encode()).hexdigest()
        row = get_cache().execute("SELECT emb,bbox,count,img_hash FROM file_cache WHERE path=?", (path,)).fetchone()
        if row and row[3] == h:
            return row[0], row[1], row[2]
    except Exception:
        pass
    return None


def cache_set(path, emb, bbox, count):
    try:
        h = hashlib.md5(str(os.path.getmtime(path)).encode()).hexdigest()
        get_cache().execute(
            "INSERT OR REPLACE INTO file_cache VALUES (?,?,?,?,?,?)",
            (path, os.path.getmtime(path), emb.tobytes(), bbox.tobytes(), count, h))
        get_cache().commit()
    except Exception:
        pass


def get_all_files(dirs):
    out = []
    for d in dirs:
        for root, _, fs in os.walk(d):
            for f in fs:
                if Path(f).suffix.lower() in IMG_EXTS:
                    out.append(os.path.join(root, f))
    return sorted(set(out))


# ---------- App ----------
class FaceFinderApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Face Finder")
        self.geometry("1150x820")
        self.minsize(950, 680)
        self.configure(fg_color="#141720")

        self.ref_paths = []
        self.search_dirs = []
        self.results = []
        self.export_dir = None
        self.scanning = False
        self._preview_img = None
        self._thumb_imgs = []

        self._build_ui()
        self._load_session()

    # ---------- UI ----------
    def _build_ui(self):
        # Header
        hdr = ctk.CTkFrame(self, fg_color="#141720", height=60)
        hdr.pack(fill="x", padx=20, pady=(16, 8))
        ctk.CTkLabel(hdr, text="👤  Face Finder", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=TEXT).pack(side="left")
        ctk.CTkLabel(hdr, text="100% local · offline · aucun upload", font=ctk.CTkFont(size=12),
                     text_color=MUTED).pack(side="left", padx=16)

        # Zone de configuration (haut)
        cfg = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14)
        cfg.pack(fill="x", padx=20, pady=8)

        # Ligne : references + dossiers
        row = ctk.CTkFrame(cfg, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=14)
        row.grid_columnconfigure(0, weight=1)
        row.grid_columnconfigure(1, weight=1)

        # Carte references
        ref_card = ctk.CTkFrame(row, fg_color="#252938", corner_radius=10)
        ref_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ctk.CTkLabel(ref_card, text="Photo référence", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT).pack(anchor="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(ref_card, text="Le visage à retrouver", font=ctk.CTkFont(size=11),
                     text_color=MUTED).pack(anchor="w", padx=14)
        ctk.CTkButton(ref_card, text="Choisir une photo", command=self.pick_ref,
                      fg_color=ACCENT, hover_color="#2563eb", height=34,
                      corner_radius=8).pack(fill="x", padx=14, pady=(10, 6))
        self.ref_label = ctk.CTkLabel(ref_card, text="Aucune photo", font=ctk.CTkFont(size=11),
                                      text_color=MUTED, wraplength=200)
        self.ref_label.pack(padx=14, pady=(0, 6))
        self.ref_thumb = ctk.CTkLabel(ref_card, text="")
        self.ref_thumb.pack(pady=(0, 12))

        # Carte dossiers
        dir_card = ctk.CTkFrame(row, fg_color="#252938", corner_radius=10)
        dir_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ctk.CTkLabel(dir_card, text="Dossiers à fouiller", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT).pack(anchor="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(dir_card, text="Scan récursif des sous-dossiers", font=ctk.CTkFont(size=11),
                     text_color=MUTED).pack(anchor="w", padx=14)
        ctk.CTkButton(dir_card, text="Ajouter un dossier", command=self.pick_dir,
                      fg_color=ACCENT, hover_color="#2563eb", height=34,
                      corner_radius=8).pack(fill="x", padx=14, pady=(10, 6))
        self.dir_list = ctk.CTkTextbox(dir_card, height=54, fg_color="#1a1d29",
                                       text_color=TEXT, font=ctk.CTkFont(size=10),
                                       corner_radius=6)
        self.dir_list.pack(fill="x", padx=14, pady=(0, 12))
        self.dir_list.insert("1.0", "Aucun dossier")
        self.dir_list.configure(state="disabled")

        # Ligne : seuil + boutons
        ctrl = ctk.CTkFrame(cfg, fg_color="transparent")
        ctrl.pack(fill="x", padx=16, pady=(0, 16))

        ctk.CTkLabel(ctrl, text="Seuil de similarité", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=TEXT).pack(side="left")
        self.thresh_var = ctk.DoubleVar(value=THRESHOLD_DEFAULT)
        ctk.CTkSlider(ctrl, from_=THRESHOLD_MIN, to=THRESHOLD_MAX, variable=self.thresh_var,
                      command=self._on_thresh, width=220, progress_color=ACCENT2,
                      button_color=ACCENT2).pack(side="left", padx=12)
        self.thresh_label = ctk.CTkLabel(ctrl, text=f"{THRESHOLD_DEFAULT:.2f}",
                                         font=ctk.CTkFont(size=13, weight="bold"), text_color=ACCENT2)
        self.thresh_label.pack(side="left")
        ctk.CTkLabel(ctrl, text="0.30 permissif  ←→  0.60 strict", font=ctk.CTkFont(size=10),
                     text_color=MUTED).pack(side="left", padx=10)

        self.cache_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(ctrl, text="Cache (scan incrémental)", variable=self.cache_var,
                        font=ctk.CTkFont(size=11), text_color=TEXT,
                        fg_color=ACCENT, hover_color="#2563eb").pack(side="right", padx=8)

        # Boutons action
        acts = ctk.CTkFrame(cfg, fg_color="transparent")
        acts.pack(fill="x", padx=16, pady=(0, 16))
        self.btn_scan = ctk.CTkButton(acts, text="▶   Lancer la recherche", command=self.start_scan,
                                      fg_color=ACCENT2, hover_color="#059669", height=40,
                                      font=ctk.CTkFont(size=14, weight="bold"), corner_radius=8)
        self.btn_scan.pack(side="left", padx=(0, 8))
        self.btn_export = ctk.CTkButton(acts, text="📥 Exporter", command=self.export_results,
                                        state="disabled", fg_color="#374151", hover_color="#4b5563",
                                        height=40, corner_radius=8)
        self.btn_export.pack(side="left", padx=4)
        self.btn_open = ctk.CTkButton(acts, text="📂 Ouvrir", command=self.open_export,
                                      state="disabled", fg_color="#374151", hover_color="#4b5563",
                                      height=40, corner_radius=8)
        self.btn_open.pack(side="left", padx=4)
        self.btn_clear = ctk.CTkButton(acts, text="🗑 Effacer", command=self.clear_results,
                                       fg_color="#374151", hover_color=DANGER,
                                       height=40, corner_radius=8)
        self.btn_clear.pack(side="left", padx=4)

        # Barre progression + statut
        prog = ctk.CTkFrame(cfg, fg_color="transparent")
        prog.pack(fill="x", padx=16, pady=(0, 14))
        self.progress = ctk.CTkProgressBar(prog, progress_color=ACCENT2, height=8, corner_radius=4)
        self.progress.pack(fill="x", side="left", expand=True)
        self.progress.set(0)
        self.status_var = ctk.StringVar(value="Prêt")
        ctk.CTkLabel(prog, textvariable=self.status_var, font=ctk.CTkFont(size=11),
                     text_color=MUTED, width=280, anchor="e").pack(side="right", padx=(12, 0))

        # Zone resultats + apercu
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(8, 20))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        # Resultats
        res = ctk.CTkFrame(body, fg_color=CARD, corner_radius=14)
        res.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(res, text="Résultats", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=TEXT).pack(anchor="w", padx=16, pady=(14, 8))

        # Treeview stylé (on garde ttk pour la perf, habillé en sombre)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("FF.Treeview", background="#1a1d29", foreground=TEXT,
                        fieldbackground="#1a1d29", rowheight=30, font=("Segoe UI", 10),
                        borderwidth=0)
        style.configure("FF.Treeview.Heading", background="#252938", foreground=MUTED,
                        font=("Segoe UI", 10, "bold"), borderwidth=0)
        style.map("FF.Treeview", background=[("selected", ACCENT)])

        self.tree = ttk.Treeview(res, columns=("f", "s", "t"), show="headings",
                                 style="FF.Treeview")
        self.tree.heading("f", text="Fichier")
        self.tree.heading("s", text="Score")
        self.tree.heading("t", text="Taille")
        self.tree.column("f", width=420, anchor="w")
        self.tree.column("s", width=80, anchor="center")
        self.tree.column("t", width=80, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        # Apercu
        prev = ctk.CTkFrame(body, fg_color=CARD, corner_radius=14)
        prev.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(prev, text="Aperçu", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=TEXT).pack(anchor="w", padx=16, pady=(14, 8))
        self.preview = ctk.CTkLabel(prev, text="Sélectionne un résultat\n\npour le prévisualiser",
                                    font=ctk.CTkFont(size=12), text_color=MUTED,
                                    fg_color="#1a1d29", corner_radius=10)
        self.preview.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self.preview_info = ctk.CTkLabel(prev, text="", font=ctk.CTkFont(size=10), text_color=MUTED)
        self.preview_info.pack(pady=(0, 14))

    # ---------- Actions ----------
    def _on_thresh(self, v=None):
        self.thresh_label.configure(text=f"{self.thresh_var.get():.2f}")

    def pick_ref(self):
        p = filedialog.askopenfilename(title="Photo de référence",
                                       filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp *.heic *.tiff"), ("Tous", "*.*")])
        if not p:
            return
        self.ref_paths = [p]
        self.ref_label.configure(text=Path(p).name, text_color=TEXT)
        try:
            im = Image.open(p).convert("RGB")
            ctk_img = CTkImage(light_image=im, dark_image=im, size=(110, 110))
            self._thumb_imgs = [ctk_img]
            self.ref_thumb.configure(image=ctk_img, text="")
        except Exception:
            self.ref_thumb.configure(image=None, text="")
        self._save_session()

    def pick_dir(self):
        d = filedialog.askdirectory(title="Dossier à fouiller")
        if not d:
            return
        if d not in self.search_dirs:
            self.search_dirs.append(d)
        self.dir_list.configure(state="normal")
        self.dir_list.delete("1.0", "end")
        self.dir_list.insert("1.0", "\n".join(self.search_dirs) if self.search_dirs else "Aucun dossier")
        self.dir_list.configure(state="disabled")
        self._save_session()

    def _load_session(self):
        try:
            cfg = json.load(open(Path.home() / ".facefinder_session.json"))
            self.search_dirs = cfg.get("dirs", [])
            if self.search_dirs:
                self.dir_list.configure(state="normal")
                self.dir_list.delete("1.0", "end")
                self.dir_list.insert("1.0", "\n".join(self.search_dirs))
                self.dir_list.configure(state="disabled")
            refs = cfg.get("refs", [])
            if refs and os.path.isfile(refs[0]):
                self.pick_ref_silent(refs[0])
        except Exception:
            pass

    def pick_ref_silent(self, p):
        self.ref_paths = [p]
        self.ref_label.configure(text=Path(p).name, text_color=TEXT)
        try:
            im = Image.open(p).convert("RGB")
            ctk_img = CTkImage(light_image=im, dark_image=im, size=(110, 110))
            self._thumb_imgs = [ctk_img]
            self.ref_thumb.configure(image=ctk_img, text="")
        except Exception:
            pass

    def _save_session(self):
        try:
            json.dump({"dirs": self.search_dirs, "refs": self.ref_paths},
                      open(Path.home() / ".facefinder_session.json", "w"))
        except Exception:
            pass

    def _set_status(self, t):
        self.after(0, lambda: self.status_var.set(t))

    def _set_progress(self, v):
        self.after(0, lambda: self.progress.set(v))

    def start_scan(self):
        if not self.ref_paths:
            messagebox.showwarning("Photo manquante", "Choisis d'abord une photo de référence.")
            return
        if not self.search_dirs:
            messagebox.showwarning("Dossier manquant", "Ajoute au moins un dossier à fouiller.")
            return
        for d in self.search_dirs:
            if not os.path.isdir(d):
                messagebox.showwarning("Dossier introuvable", f"{d}\nn'existe pas.")
                return

        self.scanning = True
        self.btn_scan.configure(state="disabled")
        self.btn_export.configure(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self.results = []
        self._set_status("Chargement du modèle…")
        threading.Thread(target=self._scan, args=(self.ref_paths[0], list(self.search_dirs)), daemon=True).start()

    def _scan(self, ref, dirs):
        try:
            engine = get_engine()
            self._set_status("Analyse de la photo de référence…")
            ref_emb, _, _ = get_embedding(ref, engine)
            if ref_emb is None:
                self._set_status("Aucun visage détecté dans la référence")
                self.after(0, self._finish_fail)
                return

            files = get_all_files(dirs)
            n = len(files)
            if n == 0:
                self._set_status("Aucune image trouvée")
                self.after(0, self._finish_fail)
                return

            self._set_status(f"{n} images à analyser…")
            thresh = self.thresh_var.get()
            use_cache = bool(self.cache_var.get())
            results = []

            for i, fp in enumerate(files):
                if not self.scanning:
                    break
                try:
                    emb, bbox, cnt = (None, None, 0)
                    if use_cache:
                        c = cache_get(fp)
                        if c:
                            emb, bbox, cnt = c
                        else:
                            emb, bbox, cnt = get_embedding(fp, engine)
                            if emb is not None:
                                cache_set(fp, emb, bbox, cnt)
                    else:
                        emb, bbox, cnt = get_embedding(fp, engine)

                    if emb is not None:
                        if cos_sim(ref_emb, emb) >= thresh:
                            results.append((fp, cos_sim(ref_emb, emb), cnt))
                except Exception:
                    pass

                self._set_progress((i + 1) / n)
                if i % 25 == 0:
                    self._set_status(f"{i + 1}/{n} · {len(results)} trouvé(s)")

            results.sort(key=lambda x: x[1], reverse=True)
            self.results = results
            self.after(0, self._show_results)
        except Exception as e:
            self._set_status(f"Erreur : {e}")
            import traceback
            traceback.print_exc()
            self.after(0, self._finish_fail)

    def _finish_fail(self):
        self.scanning = False
        self.btn_scan.configure(state="normal")
        self.progress.set(0)

    def _show_results(self):
        for fp, score, cnt in self.results:
            try:
                sz = f"{Path(fp).stat().st_size // 1024} Ko"
            except Exception:
                sz = "?"
            self.tree.insert("", "end", values=(Path(fp).name, f"{score:.3f}", sz), tags=(fp,))
        self.status_var.set(f"{len(self.results)} photo(s) trouvée(s) — seuil {self.thresh_var.get():.2f}")
        self.progress.set(0)
        self.btn_scan.configure(state="normal")
        if self.results:
            self.btn_export.configure(state="normal")

    def on_select(self, e):
        sel = self.tree.selection()
        if not sel:
            return
        item = self.tree.item(sel[0])
        tags = item.get("tags") if item else None
        if not tags:
            return
        fp = tags[0]
        if not os.path.isfile(fp):
            return
        try:
            im = Image.open(fp).convert("RGB")
            w, h = im.size
            scale = min(460 / w, 460 / h, 1)
            im2 = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            ctk_img = CTkImage(light_image=im2, dark_image=im2, size=im2.size)
            self._preview_img = ctk_img
            self.preview.configure(image=ctk_img, text="")
            self.preview_info.configure(text=Path(fp).name)
        except Exception as ex:
            self.preview_info.configure(text=str(ex))

    def export_results(self):
        if not self.results:
            return
        d = filedialog.askdirectory(title="Dossier de destination")
        if not d:
            return
        out = Path(d) / f"face-finder_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        out.mkdir(parents=True, exist_ok=True)
        for fp, score, cnt in self.results:
            dest = out / f"{score:.3f}_{Path(fp).name}"
            c = 1
            while dest.exists():
                dest = out / f"{score:.3f}_{Path(fp).stem}_{c}{Path(fp).suffix}"
                c += 1
            shutil.copy2(fp, dest)
        with open(out / "_rapport.json", "w", encoding="utf-8") as f:
            json.dump([{"fichier": fp, "score": float(s)} for fp, s, c in self.results], f,
                      indent=2, ensure_ascii=False)
        self.export_dir = str(out)
        self.btn_open.configure(state="normal")
        self._set_status(f"Exporté → {out}")
        messagebox.showinfo("Export terminé", f"{len(self.results)} photos copiées vers :\n{out}")

    def open_export(self):
        if self.export_dir and os.path.isdir(self.export_dir):
            os.startfile(self.export_dir)

    def clear_results(self):
        self.scanning = False
        self.tree.delete(*self.tree.get_children())
        self.results = []
        self._preview_img = None
        self.preview.configure(image="", text="Sélectionne un résultat\n\npour le prévisualiser")
        self.preview_info.configure(text="")
        self.progress.set(0)
        self.btn_export.configure(state="disabled")
        self.btn_open.configure(state="disabled")
        self.status_var.set("Prêt")


if __name__ == "__main__":
    FaceFinderApp().mainloop()
