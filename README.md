# Face Finder — Windows

Retrouve toutes les photos d'une personne dans un dossier. Donne une photo reference,
indique un dossier, l'app scanne et liste les matchs. **100% local, offline.**

## Lancement
```
dist\FaceFinder.exe
```
Double-clic. Aucune installation, aucun telechargement, aucun Python requis.

## Utilisation
1. **Photo reference** → le visage a retrouver (1 visage net, bien eclaire)
2. **Dossier a fouiller** → scan recursif des sous-dossiers (ajoutable en plusieurs fois)
3. **Seuil** → 0.45 par defaut. `0.30` permissif (plus de resultats, plus de faux positifs) → `0.60` strict
4. **Lancer la recherche** → barre de progression + compteur en direct
5. Cliquer un resultat → apercu a droite
6. **Exporter** → copie les photos vers `face-finder_AAAAMMJJ_HHMMSS/` + `_rapport.json`

## Formats supportes
JPG · JPEG · PNG · BMP · WEBP · HEIC/HEIF (iPhone) · TIFF

## Comment ca marche
- Detection : SCRFD (`det_10g.onnx`)
- Reconnaissance : ArcFace R50 (`w600k_r50.onnx`)
- Comparaison : embedding 512d normalise, similarite cosinus
- Cache SQLite (`~/.facefinder_cache.db`) : ne rescane que les fichiers modifies
- Session sauvegardee (`~/.facefinder_session.json`) : dossiers + photo reference au relancement

## Le bug `NoneType has no attribute 'write'` (corrige)
En mode `--windowed`, PyInstaller met `sys.stdout = None`.
InsightFace telecharge ses modeles avec **tqdm**, qui ecrit sur `sys.stdout` → crash.

**Fix en 3 points :**
1. `allowed_modules=["detection","recognition"]` → InsightFace n'exige que les 2 modeles
   bundlés et **ne telecharge plus rien** (le fix principal)
2. `_NullWriter` remplace `sys.stdout`/`sys.stderr` si `None` (filet de securite)
3. Modeles pre-bundles dans le `.exe` → aucun acces reseau au 1er lancement

## Build
```bat
C:\Users\DELL\face-finder-venv\Scripts\python.exe -m PyInstaller FaceFinder.spec --noconfirm --clean
```
Le `.exe` (~809 MB) embarque : Python, onnxruntime, numpy, scipy, skimage,
CustomTkinter, pillow-heif **et les modeles `.onnx`** (183 MB).

> Si le build echoue avec `PermissionError` sur `dist\FaceFinder.exe`,
> l'app est encore ouverte → fermer ou `Stop-Process -Name FaceFinder -Force`.

## Stack
Python 3.12 · CustomTkinter (UI) · InsightFace buffalo_l (SCRFD + ArcFace) ·
onnxruntime (CPU) · SQLite (cache) · pillow-heif (HEIC)

## Projets open-source evalues
- `lensgrep` — CLI, MIT, inspiration pour le cache SQLite
- `InsightFace Evaluation Studio` — GUI PySide6, MIT
- `face-photo-archive` — facenet-pytorch, MIT
- `FaceSort` — MIT
- `Felicity` — source-available (pas MIT)
