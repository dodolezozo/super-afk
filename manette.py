import vgamepad as vg
import keyboard
import mss
import numpy as np
import cv2
import random
import time
import threading
import os
import json

DOSSIER_SCRIPT = os.path.dirname(os.path.abspath(__file__))
FICHIER_CONFIG = os.path.join(DOSSIER_SCRIPT, "config.json")

# ---------- PARAMETRES (modifiables dans l'interface, enregistrés dans config.json) ----------

PARAMETRES_DEFAUT = {
    "TYPE_MANETTE": "ps4",        # "ps4" ou "xbox" (si Fortnite ne détecte pas la manette, essayer l'autre)
    "DUREE_SESSION": 450.0,       # secondes
    "DELAI_SEQUENCE": 1.0,        # secondes entre chaque touche de la séquence finale
    "CONFIDENCE": 0.8,            # seuil de reconnaissance des images (0 à 1)
    "INTERVALLE_SCAN": 0.3,       # secondes entre deux analyses de l'écran
    "INTERVALLE_TRIANGLE": 1.2,   # délai entre deux appuis sur Triangle (menu / skip / replay)
    "MONITEUR": 0,                # 1 = principal, 2 = secondaire, 0 = tous les écrans
    "SUICIDE": False,             # séquence spéciale quand bus.png est vu
}

def valider_parametres(brut):
    """Convertit et vérifie les paramètres. Lève ValueError avec un message clair si invalide."""
    p = {}
    t = str(brut.get("TYPE_MANETTE", PARAMETRES_DEFAUT["TYPE_MANETTE"])).strip().lower()
    if t not in ("ps4", "xbox"):
        raise ValueError("Type de manette : « ps4 » ou « xbox »")
    p["TYPE_MANETTE"] = t
    bornes = {
        "DUREE_SESSION": (1, 36000),
        "DELAI_SEQUENCE": (0.05, 30),
        "CONFIDENCE": (0.1, 1.0),
        "INTERVALLE_SCAN": (0.05, 10),
        "INTERVALLE_TRIANGLE": (0.1, 30),
    }
    for cle, (mini, maxi) in bornes.items():
        try:
            v = float(str(brut.get(cle, PARAMETRES_DEFAUT[cle])).replace(",", "."))
        except ValueError:
            raise ValueError(f"{cle} doit être un nombre")
        if not mini <= v <= maxi:
            raise ValueError(f"{cle} doit être entre {mini} et {maxi}")
        p[cle] = v
    try:
        m = int(str(brut.get("MONITEUR", PARAMETRES_DEFAUT["MONITEUR"])))
    except ValueError:
        raise ValueError("MONITEUR doit être un nombre entier")
    if not 0 <= m <= 9:
        raise ValueError("MONITEUR doit être entre 0 et 9")
    p["MONITEUR"] = m
    s = brut.get("SUICIDE", PARAMETRES_DEFAUT["SUICIDE"])
    if isinstance(s, str):
        s = s.strip().lower() in ("1", "true", "vrai", "oui", "on")
    p["SUICIDE"] = bool(s)
    return p

def lire_config():
    try:
        with open(FICHIER_CONFIG, encoding="utf-8") as f:
            return valider_parametres({**PARAMETRES_DEFAUT, **json.load(f)})
    except FileNotFoundError:
        return dict(PARAMETRES_DEFAUT)
    except Exception as e:
        print("config.json invalide, paramètres par défaut utilisés :", e)
        return dict(PARAMETRES_DEFAUT)

def parametres_actuels():
    return {cle: globals()[cle] for cle in PARAMETRES_DEFAUT}

def appliquer_parametres(brut, enregistrer=True):
    """Valide, applique tout de suite (sauf le type de manette) et enregistre dans config.json."""
    p = valider_parametres(brut)
    for cle, v in p.items():
        if cle != "TYPE_MANETTE":
            globals()[cle] = v
    if enregistrer:
        with open(FICHIER_CONFIG, "w", encoding="utf-8") as f:
            json.dump(p, f, indent=2, ensure_ascii=False)
    return p

globals().update(lire_config())
MANETTE_CHARGEE = TYPE_MANETTE       # type réellement utilisé jusqu'au prochain redémarrage

if TYPE_MANETTE == "xbox":
    pad = vg.VX360Gamepad()
    _B = vg.XUSB_BUTTON
    BTN_L3 = _B.XUSB_GAMEPAD_LEFT_THUMB
    BTN_CROIX = _B.XUSB_GAMEPAD_A
    BTN_CARRE = _B.XUSB_GAMEPAD_X
    BTN_TRIANGLE = _B.XUSB_GAMEPAD_Y
    BTN_OPTIONS = _B.XUSB_GAMEPAD_START
    BTN_R1 = _B.XUSB_GAMEPAD_RIGHT_SHOULDER
    BTN_L1 = _B.XUSB_GAMEPAD_LEFT_SHOULDER
    SENS_Y = -1      # axe Y inversé par rapport à la manette PS4
else:
    pad = vg.VDS4Gamepad()
    _B = vg.DS4_BUTTONS
    BTN_L3 = _B.DS4_BUTTON_THUMB_LEFT
    BTN_CROIX = _B.DS4_BUTTON_CROSS
    BTN_CARRE = _B.DS4_BUTTON_SQUARE
    BTN_TRIANGLE = _B.DS4_BUTTON_TRIANGLE
    BTN_OPTIONS = _B.DS4_BUTTON_OPTIONS
    BTN_R1 = _B.DS4_BUTTON_SHOULDER_RIGHT
    BTN_L1 = _B.DS4_BUTTON_SHOULDER_LEFT
    SENS_Y = 1
print("Manette virtuelle :", TYPE_MANETTE)

running = False
etat = "rien"          # "rien", "searching", "menu", "rewards", "skip", "replay", "actif", "session"

session_active = False
session_fin = 0.0
sequence_en_cours = False

DOSSIER_IMAGES = os.path.join(DOSSIER_SCRIPT, "images")       # images de référence
DOSSIER_SCREENSHOTS = os.path.join(DOSSIER_SCRIPT, "Bangerang")
os.makedirs(DOSSIER_SCREENSHOTS, exist_ok=True)

IMAGE_MENU = "menu.png"
IMAGE_SEARCHING = "searching.png"
IMAGE_ACTIF = "actif.png"
IMAGE_REWARDS = "rewards.png"
IMAGE_REPLAY = "replay.png"
IMAGE_SKIP = "skip.png"
IMAGE_BUS = "bus.png"

# Séquence « suicide » quand le bus est vu (en secondes)
BUS_ATTENTE = 15          # attente avant de bouger
BUS_POUSSEE = 10          # joystick gauche vers le haut
BUS_AVANT_PLANEUR = 5     # attente avant de déployer le planeur

bus_en_cours = False      # séquence bus en train de tourner
bus_annule = False        # demande d'arrêt de la séquence bus
bus_traite = False        # le bus actuellement à l'écran a déjà déclenché la séquence

# ---------- OUTILS ----------

def toggle():
    global running, etat, session_active, bus_traite
    running = not running
    print("ON" if running else "OFF")
    if not running:
        etat = "rien"
        session_active = False
        bus_traite = False
        pad.reset()
        pad.update()

def quitter():
    try:
        pad.reset()
        pad.update()
    except Exception:
        pass
    os._exit(0)

def rnd(a, b):
    return random.uniform(a, b)

def set_sticks(lx=0.0, ly=0.0, rx=0.0, ry=0.0):
    # ly inversé : dans ce jeu, l'axe avant/arrière du stick gauche est à l'envers
    pad.left_joystick_float(x_value_float=lx, y_value_float=-ly * SENS_Y)
    pad.right_joystick_float(x_value_float=rx, y_value_float=ry * SENS_Y)
    pad.update()

def tap(button, duree=0.12):
    pad.press_button(button=button)
    pad.update()
    time.sleep(duree)
    pad.release_button(button=button)
    pad.update()

def set_r2(valeur):
    """Gâchette R2 : 0.0 (relâchée) à 1.0 (à fond)."""
    pad.right_trigger_float(value_float=valeur)
    pad.update()

def alive():
    """Les inputs de jeu tournent tant que la session est active (et hors séquence bus)."""
    return running and session_active and not sequence_en_cours and not bus_en_cours

# ---------- DETECTION ----------

def charger(nom):
    chemin = os.path.join(DOSSIER_IMAGES, nom)
    img = cv2.imread(chemin, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Image introuvable : {chemin}")
    return img

IMAGES = {               # nom de l'état -> fichier image à reconnaître
    "menu": IMAGE_MENU,
    "searching": IMAGE_SEARCHING,
    "actif": IMAGE_ACTIF,
    "rewards": IMAGE_REWARDS,
    "replay": IMAGE_REPLAY,
    "skip": IMAGE_SKIP,
    "bus": IMAGE_BUS,
}
DOSSIER_ANCIENNES = os.path.join(DOSSIER_SCRIPT, "anciennes_images")

def recharger_templates():
    """(Re)charge les images de référence depuis le disque.
    bus.png est facultative : sans elle, l'option suicide ne fait rien."""
    global template_menu, template_searching, template_actif
    global template_rewards, template_replay, template_skip, template_bus
    template_menu = charger(IMAGE_MENU)
    template_searching = charger(IMAGE_SEARCHING)
    template_actif = charger(IMAGE_ACTIF)
    template_rewards = charger(IMAGE_REWARDS)
    template_replay = charger(IMAGE_REPLAY)
    template_skip = charger(IMAGE_SKIP)
    try:
        template_bus = charger(IMAGE_BUS)
    except FileNotFoundError:
        template_bus = None

def remplacer_template(nom, image):
    """Remplace l'image de référence `nom` (ex. "actif") par `image` (BGR).
    L'ancienne image est gardée dans le dossier anciennes_images."""
    fichier = IMAGES[nom]
    chemin = os.path.join(DOSSIER_IMAGES, fichier)
    if os.path.exists(chemin):
        os.makedirs(DOSSIER_ANCIENNES, exist_ok=True)
        sauvegarde = os.path.join(DOSSIER_ANCIENNES,
                                  time.strftime(f"{nom}_%Y-%m-%d_%H-%M-%S.png"))
        with open(chemin, "rb") as src, open(sauvegarde, "wb") as dst:
            dst.write(src.read())
    ok, png = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("Impossible d'encoder l'image")
    with open(chemin, "wb") as f:
        f.write(png.tobytes())
    recharger_templates()
    print(f"Image « {nom} » remplacée ({image.shape[1]}x{image.shape[0]})")

recharger_templates()

def score(frame, template):
    if frame.shape[0] < template.shape[0] or frame.shape[1] < template.shape[1]:
        return 0.0
    res = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    return res.max()

# ---------- CAPTURE DE LA FENETRE FORTNITE (même si elle n'a pas le focus) ----------

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # tailles en vrais pixels
except Exception:
    pass

HANDLE = ctypes.c_void_p
user32.GetDC.restype = HANDLE
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, HANDLE]
user32.PrintWindow.argtypes = [wintypes.HWND, HANDLE, wintypes.UINT]
gdi32.CreateCompatibleDC.restype = HANDLE
gdi32.CreateCompatibleDC.argtypes = [HANDLE]
gdi32.CreateCompatibleBitmap.restype = HANDLE
gdi32.CreateCompatibleBitmap.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = HANDLE
gdi32.SelectObject.argtypes = [HANDLE, HANDLE]
gdi32.DeleteObject.argtypes = [HANDLE]
gdi32.DeleteDC.argtypes = [HANDLE]
gdi32.GetDIBits.argtypes = [HANDLE, HANDLE, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
kernel32.OpenProcess.restype = HANDLE
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]

PROCESS_FORTNITE ="FortniteClient-Win64-Shipping.exe"
PW_RENDERFULLCONTENT = 0x2
_hwnd_fortnite = None
_mode_capture = None

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]

def _nom_process(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        taille = wintypes.DWORD(260)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(taille)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)

def trouver_fortnite():
    trouve = []
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and _nom_process(hwnd).lower() == PROCESS_FORTNITE.lower():
            trouve.append(hwnd)
            return False
        return True
    user32.EnumWindows(cb, 0)
    return trouve[0] if trouve else None

def capturer_fenetre(hwnd):
    """Capture le contenu de la fenêtre même si elle est recouverte (pas si elle est réduite)."""
    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right, rect.bottom
    if w <= 0 or h <= 0:
        return None
    hdc_fen = user32.GetDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_fen)
    bmp = gdi32.CreateCompatibleBitmap(hdc_fen, w, h)
    ancien = gdi32.SelectObject(hdc_mem, bmp)
    try:
        # 0x1 = PW_CLIENTONLY
        if not user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT | 0x1):
            return None
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth, bmi.biHeight = w, -h      # négatif = image de haut en bas
        bmi.biPlanes, bmi.biBitCount = 1, 32
        buf = np.empty((h, w, 4), dtype=np.uint8)
        gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf.ctypes.data, ctypes.byref(bmi), 0)
        return cv2.cvtColor(buf, cv2.COLOR_BGRA2BGR)
    finally:
        gdi32.SelectObject(hdc_mem, ancien)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_fen)

def fortnite_visible(hwnd):
    """Vrai si Fortnite est au premier plan, ou si sa fenêtre n'est recouverte par rien."""
    if user32.GetForegroundWindow() == hwnd:
        return True
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    for x, y in ((0.5, 0.5), (0.1, 0.9), (0.9, 0.9), (0.1, 0.1), (0.9, 0.1)):
        pt = wintypes.POINT(int(rect.left + (rect.right - rect.left) * x),
                            int(rect.top + (rect.bottom - rect.top) * y))
        if user32.GetAncestor(user32.WindowFromPoint(pt), 2) != hwnd:   # GA_ROOT
            return False
    return True

user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetForegroundWindow.restype = wintypes.HWND

def capturer(sct):
    """Image de Fortnite.
    - Fortnite visible : capture de l'écran (couleurs exactes, comme avant).
    - Fortnite caché derrière une autre fenêtre : capture de sa fenêtre
      (couleurs parfois un peu différentes, ex. avec l'HDR Windows)."""
    global _hwnd_fortnite, _mode_capture
    if _hwnd_fortnite is None or not user32.IsWindow(_hwnd_fortnite):
        _hwnd_fortnite = trouver_fortnite()
    frame = None
    if _hwnd_fortnite and not fortnite_visible(_hwnd_fortnite):
        frame = capturer_fenetre(_hwnd_fortnite)
        if frame is not None and frame.max() == 0:     # image noire = capture impossible
            frame = None
    if frame is not None:
        mode = "fenêtre Fortnite (cachée)"
    else:
        frame = cv2.cvtColor(np.array(sct.grab(sct.monitors[MONITEUR])), cv2.COLOR_BGRA2BGR)
        mode = "écran"
    if mode != _mode_capture:
        _mode_capture = mode
        print("Capture :", mode)
    return frame

def detecter_etat(frame):
    """Hors session : retourne l'état détecté, par ordre de priorité."""
    try:
        if score(frame, template_replay) >= CONFIDENCE:
            return "replay"
        if score(frame, template_searching) >= CONFIDENCE:
            return "searching"
        if score(frame, template_menu) >= CONFIDENCE:
            return "menu"
        if score(frame, template_rewards) >= CONFIDENCE:
            return "rewards"
        if score(frame, template_skip) >= CONFIDENCE:
            return "skip"
        if score(frame, template_actif) >= CONFIDENCE:
            return "actif"
        return "rien"
    except Exception as e:
        print("Erreur détection :", e)
        return "rien"

def detecter_replay(frame):
    """Pendant la session : ne cherche que replay.png."""
    try:
        if score(frame, template_replay) >= CONFIDENCE:
            return "replay"
        return "session"
    except Exception as e:
        print("Erreur détection :", e)
        return "session"

def screenshot_rewards(sct):
    """Sauvegarde une capture de l'écran surveillé dans le dossier Bangerang."""
    try:
        frame = capturer(sct)
        nom = time.strftime("rewards_%Y-%m-%d_%H-%M-%S.png")
        chemin = os.path.join(DOSSIER_SCREENSHOTS, nom)
        cv2.imwrite(chemin, frame)
        print(f"Screenshot sauvegardé : {chemin}")
    except Exception as e:
        print("Erreur screenshot :", e)

def diagnostic():
    """F8 : sauvegarde ce que voit le bot (écran + fenêtre) et affiche les scores."""
    try:
        dossier = os.path.join(DOSSIER_SCRIPT, "diagnostic")
        os.makedirs(dossier, exist_ok=True)
        horo = time.strftime("%H-%M-%S")
        images = {}
        with mss.MSS() as sct:
            images["ecran"] = cv2.cvtColor(np.array(sct.grab(sct.monitors[MONITEUR])), cv2.COLOR_BGRA2BGR)
        hwnd = trouver_fortnite()
        if hwnd:
            f = capturer_fenetre(hwnd)
            if f is not None:
                images["fenetre"] = f
        print(f"--- Diagnostic {horo} | Fortnite trouvé : {bool(hwnd)}"
              f" | visible : {bool(hwnd) and fortnite_visible(hwnd)} | capture utilisée : {_mode_capture}")
        templates = {"replay": template_replay, "searching": template_searching,
                     "menu": template_menu, "rewards": template_rewards,
                     "skip": template_skip, "actif": template_actif}
        if template_bus is not None:
            templates["bus"] = template_bus
        for nom, img in images.items():
            cv2.imwrite(os.path.join(dossier, f"{horo}_{nom}.png"), img)
            print(f"  {nom:8}", " ".join(f"{n}={score(img, t):.2f}" for n, t in templates.items()))
        print(f"  (seuil = {CONFIDENCE}) images dans : {dossier}")
    except Exception as e:
        print("Erreur diagnostic :", e)

def fin_de_session():
    """Arrête tout puis joue : Options, R1, R1, Croix (1 s entre chaque)."""
    global session_active, sequence_en_cours
    sequence_en_cours = True
    session_active = False
    time.sleep(0.3)          # laisse les actions en cours se terminer
    pad.reset()
    pad.update()
    print("Fin de session : séquence finale")

    for bouton in (BTN_OPTIONS, BTN_R1, BTN_R1, BTN_CROIX):
        if not running:
            break
        tap(bouton, 0.12)
        time.sleep(DELAI_SEQUENCE)

    pad.reset()
    pad.update()
    sequence_en_cours = False
    print("Séquence terminée : retour en détection")

def annuler_session():
    """Annule la session en cours et remet la manette à zéro."""
    global session_active, bus_annule
    session_active = False
    bus_annule = True
    time.sleep(0.3)          # laisse les actions en cours se terminer
    pad.reset()
    pad.update()

def sequence_bus():
    """Option suicide : met en pause les actions aléatoires (le timer de session continue),
    attend BUS_ATTENTE s, pousse le joystick gauche vers le haut BUS_POUSSEE s,
    attend BUS_AVANT_PLANEUR s, puis déploie le planeur (Croix)."""
    global bus_en_cours, bus_annule
    debut = time.time()

    def continuer():
        return running and not sequence_en_cours and not bus_annule

    def attendre(secondes):
        fin = time.time() + secondes
        while time.time() < fin:
            if not continuer():
                return False
            time.sleep(0.1)
        return True

    try:
        print(f"Bus détecté : actions en pause, attente {BUS_ATTENTE:g} s")
        time.sleep(0.3)                      # laisse l'action aléatoire en cours s'arrêter
        if not continuer():
            return
        pad.reset()
        pad.update()
        if not attendre(BUS_ATTENTE - (time.time() - debut)):
            return
        print(f"Bus : joystick gauche vers le haut pendant {BUS_POUSSEE:g} s")
        fin = time.time() + BUS_POUSSEE
        while time.time() < fin:
            if not continuer():
                return
            set_sticks(ly=1.0)
            time.sleep(0.1)
        set_sticks()
        if not attendre(BUS_AVANT_PLANEUR):
            return
        print("Bus : déploiement du planeur")
        tap(BTN_CROIX, 0.15)
        print("Séquence bus terminée : reprise des actions")
    finally:
        if not continuer() and running:
            print("Séquence bus interrompue")
        if running and not sequence_en_cours:
            set_sticks()
        bus_en_cours = False

def surveillance():
    global etat, session_active, session_fin, bus_en_cours, bus_annule, bus_traite
    dernier_triangle = 0
    rewards_traite = False
    with mss.MSS() as sct:
        while True:
            if not running or sequence_en_cours:
                time.sleep(INTERVALLE_SCAN)
                continue

            # Fin de session par timer
            if session_active and time.time() >= session_fin:
                fin_de_session()
                continue

            try:
                frame = capturer(sct)
            except Exception as e:
                print("Erreur capture :", e)
                time.sleep(INTERVALLE_SCAN)
                continue

            # Pendant la session : uniquement replay.png ; sinon scan complet
            if session_active:
                nouveau = detecter_replay(frame)
            else:
                nouveau = detecter_etat(frame)

            if nouveau != etat:
                etat = nouveau
                print("État :", etat)

            # ---- bus (option suicide) : lance la séquence une fois par apparition ----
            bus_vu = (SUICIDE and template_bus is not None and etat != "replay"
                      and score(frame, template_bus) >= CONFIDENCE)
            if not bus_vu:
                bus_traite = False
            elif not bus_traite and not bus_en_cours:
                bus_traite = True
                bus_en_cours = True
                bus_annule = False
                threading.Thread(target=sequence_bus, daemon=True).start()

            # ---- replay : prioritaire, annule même la session ----
            if etat == "replay":
                if session_active or bus_en_cours:
                    print("Replay détecté : session annulée")
                    annuler_session()
                if time.time() - dernier_triangle >= INTERVALLE_TRIANGLE:
                    tap(BTN_TRIANGLE, 0.12)
                    dernier_triangle = time.time()

            # ---- Pendant la session : tout le reste est ignoré ----
            elif session_active:
                pass

            # ---- Pendant la séquence bus (hors session) : seul « actif » compte ----
            elif bus_en_cours and etat != "actif":
                pass

            # ---- skip : Triangle (hors session) ----
            elif etat == "skip":
                if time.time() - dernier_triangle >= INTERVALLE_TRIANGLE:
                    tap(BTN_TRIANGLE, 0.12)
                    dernier_triangle = time.time()

            # ---- Hors session ----
            elif etat == "actif":
                session_active = True
                session_fin = time.time() + DUREE_SESSION
                print(f"Session démarrée : {DUREE_SESSION} s")
            elif etat == "menu":
                if time.time() - dernier_triangle >= INTERVALLE_TRIANGLE:
                    tap(BTN_TRIANGLE, 0.12)
                    dernier_triangle = time.time()
            elif etat == "rewards":
                if not rewards_traite:
                    screenshot_rewards(sct)      # capture AVANT l'appui
                    tap(BTN_CROIX, 0.12)
                    rewards_traite = True
            # searching / rien : on ne fait rien

            # Réarme le déclencheur quand l'écran rewards a disparu
            if etat != "rewards":
                rewards_traite = False

            time.sleep(INTERVALLE_SCAN)

# ---------- COMPORTEMENTS ----------

def avancer(duree=None):
    """Avance, avec parfois un saut au milieu."""
    duree = duree or rnd(1.5, 5)
    fin = time.time() + duree
    saut_a = time.time() + rnd(0.5, duree) if random.random() < 0.3 else None
    while time.time() < fin and alive():
        set_sticks(ly=rnd(0.7, 1.0), rx=rnd(-0.15, 0.15), ry=rnd(-0.03, 0.03))
        if saut_a and time.time() >= saut_a:
            tap(BTN_CROIX, rnd(0.08, 0.2))
            saut_a = None
        time.sleep(rnd(0.08, 0.2))
    set_sticks()

def avancer_diagonale():
    sens = random.choice([-1, 1])
    fin = time.time() + rnd(0.8, 3)
    while time.time() < fin and alive():
        set_sticks(lx=sens * rnd(0.5, 0.9), ly=rnd(0.6, 1.0), rx=rnd(-0.1, 0.1))
        time.sleep(rnd(0.08, 0.2))
    set_sticks()

def avancer_et_tourner():
    sens = random.choice([-1, 1])
    fin = time.time() + rnd(1, 3.5)
    force = rnd(0.25, 0.6)
    while time.time() < fin and alive():
        set_sticks(ly=rnd(0.7, 1.0), rx=sens * force * rnd(0.8, 1.1))
        time.sleep(0.05)
    set_sticks()

def regarder_autour():
    sens = random.choice([-1, 1])
    fin = time.time() + rnd(0.6, 2.5)
    while time.time() < fin and alive():
        set_sticks(rx=sens * rnd(0.3, 0.8), ry=rnd(-0.3, 0.3))
        time.sleep(0.05)
    set_sticks()

def balayage_camera():
    sens = random.choice([-1, 1])
    for s in (sens, -sens):
        fin = time.time() + rnd(0.4, 1.2)
        while time.time() < fin and alive():
            set_sticks(rx=s * rnd(0.4, 0.8), ry=rnd(-0.1, 0.1))
            time.sleep(0.05)
        set_sticks()
        time.sleep(rnd(0.1, 0.4))

def courir():
    """Sprint (L3) en avançant."""
    pad.press_button(button=BTN_L3)
    pad.update()
    fin = time.time() + rnd(2, 5)
    while time.time() < fin and alive():
        set_sticks(ly=1.0, rx=rnd(-0.2, 0.2))
        time.sleep(rnd(0.08, 0.2))
    pad.release_button(button=BTN_L3)
    set_sticks()

def courir_sauter():
    """Sprint + sauts répétés à intervalles irréguliers."""
    pad.press_button(button=BTN_L3)
    pad.update()
    fin = time.time() + rnd(2.5, 6)
    prochain_saut = time.time() + rnd(0.4, 1.2)
    while time.time() < fin and alive():
        set_sticks(ly=1.0, rx=rnd(-0.2, 0.2))
        if time.time() >= prochain_saut:
            tap(BTN_CROIX, rnd(0.08, 0.2))
            prochain_saut = time.time() + rnd(0.6, 1.6)
        time.sleep(rnd(0.06, 0.15))
    pad.release_button(button=BTN_L3)
    set_sticks()

def courir_diagonale():
    """Sprint en diagonale, avec saut éventuel."""
    sens = random.choice([-1, 1])
    pad.press_button(button=BTN_L3)
    pad.update()
    fin = time.time() + rnd(1.5, 3.5)
    saut_a = time.time() + rnd(0.3, 1.5) if random.random() < 0.5 else None
    while time.time() < fin and alive():
        set_sticks(lx=sens * rnd(0.4, 0.8), ly=1.0, rx=rnd(-0.1, 0.1))
        if saut_a and time.time() >= saut_a:
            tap(BTN_CROIX, rnd(0.08, 0.2))
            saut_a = None
        time.sleep(rnd(0.06, 0.15))
    pad.release_button(button=BTN_L3)
    set_sticks()

def sauter_en_avancant():
    lx = random.choice([0, 0, -0.6, 0.6])
    set_sticks(lx=lx, ly=1.0)
    time.sleep(rnd(0.1, 0.4))
    if alive():
        tap(BTN_CROIX, rnd(0.08, 0.2))
    time.sleep(rnd(0.4, 1.0))
    set_sticks()

def sauter_sur_place():
    if alive():
        tap(BTN_CROIX, rnd(0.08, 0.2))
    time.sleep(rnd(0.3, 0.8))

def tirer():
    """Tir sur place : R2 maintenu brièvement ou en rafale."""
    set_sticks()
    time.sleep(rnd(0.1, 0.4))
    rafales = random.choice([1, 1, 2, 3])
    for _ in range(rafales):
        if not alive():
            break
        set_r2(rnd(0.85, 1.0))
        time.sleep(rnd(0.1, 0.6))
        set_r2(0.0)
        time.sleep(rnd(0.1, 0.4))
    set_r2(0.0)

def tirer_en_bougeant():
    """Tir tout en avançant ou en strafant."""
    sens = random.choice([-1, 0, 1])
    fin = time.time() + rnd(1, 3)
    prochain_tir = time.time() + rnd(0.1, 0.5)
    tir_fin = 0
    while time.time() < fin and alive():
        set_sticks(lx=sens * rnd(0.4, 0.8), ly=rnd(0.5, 1.0), rx=rnd(-0.15, 0.15))
        now = time.time()
        if now >= prochain_tir:
            set_r2(rnd(0.85, 1.0))
            tir_fin = now + rnd(0.1, 0.5)
            prochain_tir = now + rnd(0.5, 1.5)
        if tir_fin and now >= tir_fin:
            set_r2(0.0)
            tir_fin = 0
        time.sleep(0.06)
    set_r2(0.0)
    set_sticks()

def interagir():
    set_sticks()
    time.sleep(rnd(0.2, 0.6))
    if alive():
        tap(BTN_CARRE, rnd(0.1, 0.3))
    time.sleep(rnd(0.3, 1.0))

def reculer():
    """Petit pas en arrière, court."""
    fin = time.time() + rnd(0.3, 0.9)
    while time.time() < fin and alive():
        set_sticks(ly=-rnd(0.5, 0.9), rx=rnd(-0.1, 0.1))
        time.sleep(0.1)
    set_sticks()

def reculer_longtemps():
    """Recul plus long que reculer(), avec parfois un léger décalage."""
    fin = time.time() + rnd(1.2, 3.0)
    lx = random.choice([0, 0, -0.3, 0.3])
    while time.time() < fin and alive():
        set_sticks(lx=lx, ly=-rnd(0.6, 1.0), rx=rnd(-0.1, 0.1))
        time.sleep(rnd(0.08, 0.2))
    set_sticks()

def aller_gauche():
    """Strafe à gauche."""
    fin = time.time() + rnd(0.6, 2.2)
    while time.time() < fin and alive():
        set_sticks(lx=-rnd(0.6, 1.0), ly=rnd(-0.1, 0.2), rx=rnd(-0.1, 0.1))
        time.sleep(rnd(0.08, 0.2))
    set_sticks()

def aller_droite():
    """Strafe à droite."""
    fin = time.time() + rnd(0.6, 2.2)
    while time.time() < fin and alive():
        set_sticks(lx=rnd(0.6, 1.0), ly=rnd(-0.1, 0.2), rx=rnd(-0.1, 0.1))
        time.sleep(rnd(0.08, 0.2))
    set_sticks()

def changer_arme():
    """Change d'arme avec R1 ou L1, choisis au hasard à chaque fois."""
    set_sticks()
    time.sleep(rnd(0.1, 0.4))
    if not alive():
        return
    bouton = random.choice([BTN_R1, BTN_L1])
    tap(bouton, rnd(0.08, 0.15))
    time.sleep(rnd(0.25, 0.6))

def pause():
    set_sticks()
    time.sleep(rnd(0.5, 2.5))

ACTIONS = {
    "avancer": avancer,
    "diagonale": avancer_diagonale,
    "avancer_tourner": avancer_et_tourner,
    "regarder": regarder_autour,
    "balayage": balayage_camera,
    "courir": courir,
    "courir_sauter": courir_sauter,
    "courir_diagonale": courir_diagonale,
    "sauter_avancer": sauter_en_avancant,
    "sauter": sauter_sur_place,
    "tirer": tirer,
    "tirer_bougeant": tirer_en_bougeant,
    "interagir": interagir,
    "reculer": reculer,
    "reculer_long": reculer_longtemps,
    "gauche": aller_gauche,
    "droite": aller_droite,
    "changer_arme": changer_arme,
    "pause": pause,
}

TRANSITIONS = {
    "debut": {"avancer": 5, "avancer_tourner": 3, "regarder": 2},
    "avancer": {"avancer_tourner": 4, "diagonale": 3, "sauter_avancer": 3,
                "regarder": 3, "courir": 3, "courir_sauter": 2,
                "tirer_bougeant": 2, "tirer": 1, "avancer": 2, "interagir": 1,
                "gauche": 2, "droite": 2, "changer_arme": 1},
    "diagonale": {"avancer": 4, "avancer_tourner": 3, "sauter_avancer": 2,
                  "courir_diagonale": 2, "tirer_bougeant": 2, "regarder": 2,
                  "diagonale": 1, "gauche": 1, "droite": 1},
    "avancer_tourner": {"avancer": 5, "diagonale": 2, "regarder": 2,
                        "sauter_avancer": 2, "courir": 2, "tirer": 1},
    "regarder": {"avancer": 6, "avancer_tourner": 3, "balayage": 1,
                 "interagir": 1, "courir": 1, "tirer": 2,
                 "gauche": 2, "droite": 2, "changer_arme": 1},
    "balayage": {"avancer": 5, "avancer_tourner": 3, "tirer": 2, "pause": 1,
                 "gauche": 1, "droite": 1},
    "courir": {"avancer": 3, "courir_sauter": 3, "sauter_avancer": 2,
               "courir_diagonale": 2, "regarder": 2, "avancer_tourner": 2,
               "tirer_bougeant": 1},
    "courir_sauter": {"avancer": 4, "courir": 2, "regarder": 2,
                      "avancer_tourner": 2, "tirer": 1},
    "courir_diagonale": {"avancer": 4, "courir": 2, "regarder": 2,
                         "avancer_tourner": 2, "tirer_bougeant": 1},
    "sauter_avancer": {"avancer": 5, "avancer_tourner": 3, "regarder": 2,
                       "courir": 2, "tirer_bougeant": 1},
    "sauter": {"avancer": 5, "regarder": 2, "avancer_tourner": 2},
    "tirer": {"avancer": 4, "regarder": 3, "avancer_tourner": 2,
              "diagonale": 2, "tirer": 1, "reculer": 1,
              "reculer_long": 1, "changer_arme": 1},
    "tirer_bougeant": {"avancer": 4, "regarder": 2, "courir": 2,
                       "avancer_tourner": 2, "tirer": 1},
    "interagir": {"avancer": 6, "regarder": 3, "pause": 1, "reculer": 1,
                  "reculer_long": 1, "changer_arme": 1},
    "reculer": {"regarder": 3, "avancer_tourner": 4, "avancer": 4,
                "diagonale": 2},
    "reculer_long": {"avancer": 4, "avancer_tourner": 3, "regarder": 3,
                     "diagonale": 2, "tirer": 1},
    "gauche": {"avancer": 5, "avancer_tourner": 3, "regarder": 2,
               "droite": 1, "tirer": 2, "courir": 1},
    "droite": {"avancer": 5, "avancer_tourner": 3, "regarder": 2,
               "gauche": 1, "tirer": 2, "courir": 1},
    "changer_arme": {"avancer": 5, "regarder": 3, "tirer": 2,
                     "avancer_tourner": 2},
    "pause": {"regarder": 3, "avancer": 5, "balayage": 1,
              "avancer_tourner": 2, "tirer": 1,
              "gauche": 1, "droite": 1, "reculer_long": 1},
}

def choisir(precedente):
    poids = TRANSITIONS.get(precedente, TRANSITIONS["debut"])
    return random.choices(list(poids.keys()), weights=list(poids.values()), k=1)[0]

def boucle():
    precedente = "debut"
    while True:
        if not alive():
            precedente = "debut"
            time.sleep(0.1)
            continue
        action = choisir(precedente)
        ACTIONS[action]()
        precedente = action
        if alive() and random.random() < 0.35:
            time.sleep(rnd(0.05, 0.4))

# ---------- LANCEMENT ----------

_demarre = False

def demarrer():
    """Lance les raccourcis clavier et les deux threads du bot (une seule fois)."""
    global _demarre
    if _demarre:
        return
    _demarre = True
    keyboard.add_hotkey("f9", toggle)
    keyboard.add_hotkey("f10", quitter)
    keyboard.add_hotkey("f8", lambda: threading.Thread(target=diagnostic, daemon=True).start())
    threading.Thread(target=surveillance, daemon=True).start()
    threading.Thread(target=boucle, daemon=True).start()
    print("Prêt : F9 = ON/OFF | F10 = quitter | F8 = diagnostic")

if __name__ == "__main__":
    demarrer()
    keyboard.wait()