@echo off
REM ============================================================
REM  INSTALLEUR / EXECUTEUR — Face Finder (v2, 851 Mo standalone)
REM  Fichier source: C:\Users\DELL\face-finder\dist\FaceFinder.exe
REM ============================================================
setlocal EnableDelayedExpansion

title Installation — Face Finder

REM --- Vérifier que l'exécutable source existe ---
if not exist "C:\Users\DELL\face-finder\dist\FaceFinder.exe" (
    echo [ERREUR] Fichier source non trouvé.
    echo         Source attendue : C:\Users\DELL\face-finder\dist\FaceFinder.exe
    echo         Vérifie que le build PyInstaller a réussi.
    pause
    exit /b 1
)

REM --- Dossier d'installation (exécutable portable) ---
set "INSTALL_DIR=%ProgramFiles%\FaceFinder"
echo [1/4] Création du dossier d'installation...
mkdir "%INSTALL_DIR%" 2>nul

REM --- Copie du .exe ---
echo [2/4] Copie de FaceFinder.exe (851 Mo — quelques secondes)...
copy /Y "C:\Users\DELL\face-finder\dist\FaceFinder.exe" "%INSTALL_DIR%\FaceFinder.exe" >nul

REM --- Création d'un raccourci sur le bureau ---
echo [3/4] Raccourci sur le bureau...
set "DESKTOP=%USERPROFILE%\Desktop"
powershell -NoProfile -Command "
$Wsh = New-Object -ComObject WScript.Shell;
$Shortcut = $Wsh.CreateShortcut('%DESKTOP%\Face Finder.lnk');
$Shortcut.TargetPath = '%INSTALL_DIR%\FaceFinder.exe';
$Shortcut.WorkingDirectory = '%INSTALL_DIR%';
$Shortcut.Description = 'Face Finder — Recherche faciale hors-ligne';
$Shortcut.IconLocation = '%INSTALL_DIR%\FaceFinder.exe,0';
$Shortcut.Save();
"

REM --- Ajout au menu Démarrer ---
echo [4/4] Ajout au menu Démarrer...
mkdir "%APPDATA%\Microsoft\Windows\Start Menu\Programs\FaceFinder" 2>nul
copy /Y "%DESKTOP%\Face Finder.lnk" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\FaceFinder\" >nul

REM --- Message final ---
echo.
echo ============================================================
echo  INSTALLATION TERMINÉE — Face Finder v2.0
REM ============================================================
echo.
echo  Le fichier exécutable a été copié ici :
echo     %INSTALL_DIR%\FaceFinder.exe
REM ============================================================
echo  Raccourci créé sur le bureau : "Face Finder.lnk"
REM  Menu Démarrer : "FaceFinder"
echo.
echo  L'application est 100%% autonome (pas besoin de Python).
echo  Au 1er lancement, le modèle AI (buffalo_l) est déjà inclus.
echo  Aucun téléchargement, aucune connexion Internet requise.
echo ============================================================
pause
REM ============================================================
REM  LANCEMENT IMMÉDIAT (facultatif — décommente la ligne ci-dessous)
REM  pour démarrer directement après installation :
REM ============================================================
REM start "" "%INSTALL_DIR%\FaceFinder.exe"
