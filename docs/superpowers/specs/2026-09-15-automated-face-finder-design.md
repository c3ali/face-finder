# Spécification — Automated Face Finder (branche automated-face-finder)

Date : 2026-09-15
Branche : https://github.com/c3ali/face-finder/tree/automated-face-finder

## 1. Objectif
Agent qui reçoit une photo de référence via Telegram (numéro autorisé 904941272),
scanne un dossier Google Drive configuré, et renvoie les photos qui matchent —
avant envoi final par un humain qui valide chaque résultat.

## 2. Architecture (approche A — locale)
- Agent Telegram (polling) : reçoit photo, répond avec boutons inline
- Google Drive Sync (rclone) : copie locale dans /tmp/gdrive_copy/
- Scanner (FaceFinder .exe existant) : match avec modèle buffalo_l
- Interface validation (inline keyboard Telegram) : 2 étapes d'autorisation
- Envoi final (python-telegram-bot send_photo)

## 3. Workflow détaillé
1. Photo reçue sur Telegram → stockée /tmp/telegram_refs/
2. Humain reçoit message : "Demande reçue. Autoriser le scan ?" [Oui] [Non]
3. Si Oui : rclone sync gdrive:scandrive /tmp/gdrive_copy/
4. Lancement scan (subprocess : FaceFinder.exe avec arguments CLI ou appel Python direct au module)
5. Résultat : liste des photos (nom + score cosinus) dans un message
6. Pour chaque photo : bouton inline [Valider] [Ignorer]
7. Quand au moins 1 photo validée : bouton [Envoyer X photos approuvées]
8. Envoi des photos validées au même numéro Telegram (avec légende du score)
9. Nettoyage /tmp (photos supprimées après envoi ou après 24h)

## 4. Sécurité
- Whitelist Telegram : seul 904941272 peut déclencher
- Token Telegram dans .env (non commit)
- Photos locales uniquement, supprimées après envoi
- Aucun upload tiers

## 5. Fichiers créés
- telegram_agent/bot.py, scanner.py, validator.py, gdrive_sync.py, sender.py
- telegram_agent/.env (exclu du .git via .gitignore)
- scripts/install_rclone.bat, scripts/setup_gdrive.bat
- docs/superpowers/specs/2026-09-15-automated-face-finder-design.md

## 6. Limites / non couvert
- Le PC local doit rester allumé (polling Telegram)
- Le dossier Google Drive doit être monté/configuré une fois (rclone token)
- Les modèles AI sont déjà dans le .exe FaceFinder (aucun téléchargement au scan)
