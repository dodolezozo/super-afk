"""Vérifie et installe les mises à jour de super afk depuis GitHub."""

import io
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import zipfile

DEPOT = "dodolezozo/super-afk"
BRANCHE = "main"
URL_VERSION = f"https://raw.githubusercontent.com/{DEPOT}/{BRANCHE}/version.txt"
URL_ZIP = f"https://codeload.github.com/{DEPOT}/zip/refs/heads/{BRANCHE}"

DOSSIER = os.path.dirname(os.path.abspath(__file__))
FICHIER_VERSION = os.path.join(DOSSIER, "version.txt")

# Jamais écrasés par une mise à jour : réglages et fichiers propres à ce PC.
FICHIERS_PROTEGES = {"config.json"}
DOSSIERS_PROTEGES = {".git", "Bangerang", "anciennes_images", "diagnostic", "__pycache__"}
# Les images de référence refaites sur ce PC sont gardées ; seules les manquantes sont ajoutées.
DOSSIER_IMAGES = "images"


def version_locale():
    try:
        with open(FICHIER_VERSION, encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def _decouper(version):
    return tuple(int(n) for n in re.findall(r"\d+", version)) or (0,)


def est_plus_recente(distante, locale):
    return _decouper(distante) > _decouper(locale)


def _telecharger(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "super-afk", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def version_distante():
    """Version publiée sur GitHub. Lève une exception si GitHub est injoignable."""
    try:
        texte = _telecharger(f"{URL_VERSION}?t={int(time.time())}").decode("utf-8").strip()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError("pas de version.txt sur GitHub (pas encore poussé ?)")
        raise
    if not re.fullmatch(r"\d+(\.\d+)*", texte):
        raise ValueError(f"version.txt sur GitHub invalide : {texte!r}")
    return texte


def _git_disponible():
    return os.path.isdir(os.path.join(DOSSIER, ".git")) and shutil.which("git") is not None


def mettre_a_jour():
    """Installe la dernière version. Retourne un message à afficher.
    - Dossier cloné avec git : `git pull --ff-only` (refuse plutôt que d'écraser du travail local).
    - Sinon : télécharge le zip de GitHub et remplace les fichiers du programme."""
    if _git_disponible():
        r = subprocess.run(["git", "pull", "--ff-only", "origin", BRANCHE], cwd=DOSSIER,
                           capture_output=True, text=True, timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise RuntimeError("git pull a échoué :\n" + (r.stderr or r.stdout).strip())
        return "Mise à jour via git :\n" + (r.stdout.strip() or "ok")

    archive = zipfile.ZipFile(io.BytesIO(_telecharger(URL_ZIP, timeout=60)))
    dossier_reel = os.path.realpath(DOSSIER)
    ecrits, gardes = 0, 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        # Le zip contient un dossier racine "super-afk-main/" : on l'enlève.
        morceaux = info.filename.split("/", 1)
        if len(morceaux) < 2 or not morceaux[1]:
            continue
        relatif = morceaux[1]
        premier = relatif.split("/", 1)[0]
        if premier in DOSSIERS_PROTEGES or relatif in FICHIERS_PROTEGES:
            continue
        cible = os.path.realpath(os.path.join(DOSSIER, *relatif.split("/")))
        if os.path.commonpath([cible, dossier_reel]) != dossier_reel:
            continue  # chemin suspect qui sortirait du dossier
        if premier == DOSSIER_IMAGES and os.path.exists(cible):
            gardes += 1
            continue
        os.makedirs(os.path.dirname(cible), exist_ok=True)
        temporaire = cible + ".maj"
        with open(temporaire, "wb") as f:
            f.write(archive.read(info))
        os.replace(temporaire, cible)
        ecrits += 1
    return (f"{ecrits} fichiers mis à jour.\n"
            f"{gardes} images de référence existantes gardées telles quelles.")
