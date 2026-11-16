"""super afk : interface graphique du bot (manette.py)."""

import sys
import os
import math
import time
import queue
import base64
import tkinter as tk
from tkinter import ttk, messagebox

# ---------- Redirection des messages du bot vers l'interface ----------

_messages = queue.Queue()

class _Flux:
    def __init__(self, original):
        self.original = original
    def write(self, texte):
        _messages.put(texte)
        if self.original is not None:
            try:
                self.original.write(texte)
            except Exception:
                pass
    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass

sys.stdout = _Flux(sys.stdout)
sys.stderr = _Flux(sys.stderr)

try:
    import cv2
    import mss
    import threading
    import subprocess
    import mise_a_jour as maj
    import manette as bot
except Exception as e:  # ex. pilote ViGEmBus absent, image manquante...
    racine = tk.Tk()
    racine.withdraw()
    messagebox.showerror("super afk", f"Impossible de démarrer le bot :\n\n{e}")
    sys.exit(1)

# ---------- Couleurs ----------

FOND = "#14161c"
CARTE = "#1d2029"
BORD = "#2c3040"
TEXTE = "#e8eaf0"
GRIS = "#8b90a0"
BLEU = "#3d8bfd"
VERT = "#2ecc71"
ROUGE = "#e74c3c"
POLICE = "Segoe UI"

LIBELLES_PARAMETRES = [
    ("TYPE_MANETTE", "Type de manette"),
    ("DUREE_SESSION", "Durée d'une session (s)"),
    ("DELAI_SEQUENCE", "Délai séquence finale (s)"),
    ("CONFIDENCE", "Seuil de reconnaissance (0 à 1)"),
    ("INTERVALLE_SCAN", "Intervalle d'analyse (s)"),
    ("INTERVALLE_TRIANGLE", "Délai entre deux Triangle (s)"),
    ("MONITEUR", "Écran surveillé (0 = tous)"),
]

LIBELLES_IMAGES = {
    "menu": "Menu (bouton Play)",
    "searching": "Recherche de partie",
    "actif": "En jeu (barre de vie)",
    "rewards": "Récompenses",
    "replay": "Replay",
    "skip": "Skip",
}


def formater(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def image_tk(image_bgr, largeur_max, hauteur_max):
    """Convertit une image OpenCV en PhotoImage, réduite pour tenir dans le cadre."""
    h, w = image_bgr.shape[:2]
    echelle = min(largeur_max / w, hauteur_max / h, 1.0)
    if echelle < 1.0:
        image_bgr = cv2.resize(image_bgr, (max(1, int(w * echelle)), max(1, int(h * echelle))),
                               interpolation=cv2.INTER_AREA)
    ok, png = cv2.imencode(".png", image_bgr)
    return tk.PhotoImage(data=base64.b64encode(png.tobytes()).decode("ascii"), format="png")


# ---------- Étoile qui tourne ----------

class Etoile(tk.Canvas):
    def __init__(self, parent, taille=96):
        super().__init__(parent, width=taille, height=taille, bg=FOND, highlightthickness=0)
        self.c = taille / 2
        self.r = taille * 0.44
        self.angle = 0.0
        self.t1 = self.create_polygon(0, 0, 0, 0, 0, 0, outline=BLEU, fill="", width=5, joinstyle="miter")
        self.t2 = self.create_polygon(0, 0, 0, 0, 0, 0, outline=BLEU, fill="", width=5, joinstyle="miter")
        self.tourner()

    def _triangle(self, depart):
        pts = []
        for k in range(3):
            a = math.radians(self.angle + depart + k * 120)
            pts += [self.c + self.r * math.cos(a), self.c + self.r * math.sin(a)]
        return pts

    def tourner(self):
        self.coords(self.t1, *self._triangle(-90))
        self.coords(self.t2, *self._triangle(90))
        self.angle = (self.angle + 2) % 360
        self.after(30, self.tourner)


# ---------- Sélection d'une zone sur une capture ----------

class SelecteurZone(tk.Toplevel):
    """Affiche la capture en plein écran ; on trace un cadre à la souris."""

    def __init__(self, parent, frame, nom, quand_fini):
        super().__init__(parent)
        self.frame = frame
        self.quand_fini = quand_fini
        self.depart = None
        self.zone = None  # (x0, y0, x1, y1) en pixels de la capture

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{sw}x{sh}+0+0")

        h, w = frame.shape[:2]
        self.echelle = min(sw / w, sh / h, 1.0)
        self.ox = (sw - w * self.echelle) / 2
        self.oy = (sh - h * self.echelle) / 2
        self.img_w, self.img_h = w * self.echelle, h * self.echelle

        self.canvas = tk.Canvas(self, width=sw, height=sh, bg="black",
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack()
        self.photo = image_tk(frame, sw, sh)
        self.canvas.create_image(self.ox, self.oy, image=self.photo, anchor="nw")
        self.cadre = self.canvas.create_rectangle(0, 0, 0, 0, outline="#ff3b3b", width=2, state="hidden")

        consigne = (f"Trace un cadre autour de « {LIBELLES_IMAGES.get(nom, nom)} »   •   "
                    "Entrée = valider   •   Échap = annuler   •   tu peux retracer le cadre")
        t = self.canvas.create_text(sw / 2, 24, text=consigne, fill="white",
                                    font=(POLICE, 13, "bold"))
        x0, y0, x1, y1 = self.canvas.bbox(t)
        fond = self.canvas.create_rectangle(x0 - 14, y0 - 8, x1 + 14, y1 + 8,
                                            fill="#000000", outline=BLEU, width=2)
        self.canvas.tag_lower(fond, t)
        self.info = self.canvas.create_text(sw / 2, 58, text="", fill="#ffd166",
                                            font=(POLICE, 11, "bold"))

        self.canvas.bind("<ButtonPress-1>", self._presse)
        self.canvas.bind("<B1-Motion>", self._glisse)
        self.canvas.bind("<ButtonRelease-1>", self._glisse)
        self.bind("<Return>", self._valider)
        self.bind("<KP_Enter>", self._valider)
        self.bind("<Escape>", lambda e: self._fermer(None))

        self.after(50, self._prendre_focus)

    def _prendre_focus(self):
        self.lift()
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _vers_image(self, x, y):
        x = min(max(x, self.ox), self.ox + self.img_w)
        y = min(max(y, self.oy), self.oy + self.img_h)
        return (x - self.ox) / self.echelle, (y - self.oy) / self.echelle

    def _presse(self, e):
        self.depart = self._vers_image(e.x, e.y)
        self.zone = None

    def _glisse(self, e):
        if self.depart is None:
            return
        fx, fy = self._vers_image(e.x, e.y)
        h, w = self.frame.shape[:2]
        x0, x1 = sorted((int(round(self.depart[0])), int(round(fx))))
        y0, y1 = sorted((int(round(self.depart[1])), int(round(fy))))
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        self.zone = (x0, y0, x1, y1)
        e2 = self.echelle
        self.canvas.coords(self.cadre, self.ox + x0 * e2, self.oy + y0 * e2,
                           self.ox + x1 * e2, self.oy + y1 * e2)
        self.canvas.itemconfigure(self.cadre, state="normal")
        self.canvas.itemconfigure(self.info, text=f"{x1 - x0} x {y1 - y0} px")

    def _valider(self, _=None):
        if not self.zone:
            self.canvas.itemconfigure(self.info, text="Trace d'abord un cadre avec la souris")
            return
        x0, y0, x1, y1 = self.zone
        if x1 - x0 < 8 or y1 - y0 < 8:
            self.canvas.itemconfigure(self.info, text="Cadre trop petit (8 x 8 px minimum)")
            return
        self._fermer(self.frame[y0:y1, x0:x1].copy())

    def _fermer(self, resultat):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        self.quand_fini(resultat)


# ---------- Fenêtre principale ----------

class Interface(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("super afk")
        self.configure(bg=FOND)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", bot.quitter)
        self._style()

        self.vars_param = {}
        self.vignettes = {}
        self.boutons_refaire = []
        self.capture_en_cours = False

        self._entete()
        corps = tk.Frame(self, bg=FOND)
        corps.pack(fill="both", padx=16, pady=(0, 4))
        gauche = tk.Frame(corps, bg=FOND)
        gauche.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        droite = tk.Frame(corps, bg=FOND)
        droite.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self._bloc_etat(gauche)
        self._bloc_parametres(gauche)
        self._bloc_images(droite)
        self._bloc_journal(droite)
        self._pied_de_page()

        bot.demarrer()
        self._rafraichir()
        self._verifier_maj(silencieux=True)

    # ----- version et mise à jour (en bas à droite) -----
    def _pied_de_page(self):
        pied = tk.Frame(self, bg=FOND)
        pied.pack(fill="x", padx=16, pady=(0, 12))
        self.btn_maj = self._bouton(pied, "Mettre à jour", self._verifier_maj, BORD)
        self.btn_maj.pack(side="right")
        tk.Label(pied, text=f"v{maj.version_locale()}", bg=FOND, fg=GRIS,
                 font=(POLICE, 10, "bold")).pack(side="right", padx=10)
        self.lbl_maj = tk.Label(pied, text="", bg=FOND, fg=GRIS, font=(POLICE, 9))
        self.lbl_maj.pack(side="right")

    def _en_fond(self, fonction, suite):
        """Lance `fonction` dans un thread puis appelle `suite(ok, résultat)` dans l'interface."""
        resultat = queue.Queue()
        def travail():
            try:
                resultat.put((True, fonction()))
            except Exception as e:
                resultat.put((False, e))
        threading.Thread(target=travail, daemon=True).start()
        def attendre():
            try:
                ok, r = resultat.get_nowait()
            except queue.Empty:
                self.after(100, attendre)
                return
            suite(ok, r)
        self.after(100, attendre)

    def _verifier_maj(self, silencieux=False):
        self.btn_maj.configure(state="disabled")
        self.lbl_maj.configure(text="Vérification sur GitHub…", fg=GRIS)
        self._en_fond(maj.version_distante, lambda ok, r: self._resultat_verif(ok, r, silencieux))

    def _resultat_verif(self, ok, distante, silencieux):
        self.btn_maj.configure(state="normal")
        locale = maj.version_locale()
        if not ok:
            self.lbl_maj.configure(text="Vérification impossible", fg=ROUGE)
            if not silencieux:
                messagebox.showerror("super afk", f"Impossible de vérifier les mises à jour :\n{distante}",
                                     parent=self)
            return
        if not maj.est_plus_recente(distante, locale):
            self.lbl_maj.configure(text="À jour", fg=VERT)
            if not silencieux:
                messagebox.showinfo("super afk", f"Tu as déjà la dernière version (v{locale}).", parent=self)
            return
        self.lbl_maj.configure(text=f"v{distante} disponible", fg="#ffd166")
        print(f"Nouvelle version disponible : v{distante} (tu as v{locale})")
        if silencieux:
            return
        if messagebox.askyesno("super afk",
                               f"La version v{distante} est disponible (tu as v{locale}).\n\n"
                               "Installer la mise à jour ?\n"
                               "Tes paramètres, tes images de référence et tes screenshots sont gardés.",
                               parent=self):
            self.btn_maj.configure(state="disabled")
            self.lbl_maj.configure(text="Installation…", fg=GRIS)
            self._en_fond(maj.mettre_a_jour, self._resultat_maj)

    def _resultat_maj(self, ok, message):
        self.btn_maj.configure(state="normal")
        if not ok:
            self.lbl_maj.configure(text="Échec de la mise à jour", fg=ROUGE)
            messagebox.showerror("super afk", f"La mise à jour a échoué :\n{message}", parent=self)
            return
        self.lbl_maj.configure(text="Mise à jour installée", fg=VERT)
        print(message)
        if messagebox.askyesno("super afk", f"{message}\n\nRedémarrer super afk maintenant ?", parent=self):
            subprocess.Popen([sys.executable, os.path.join(maj.DOSSIER, "interface.py")], cwd=maj.DOSSIER)
            bot.quitter()

    # ----- style -----
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TCombobox", fieldbackground=CARTE, background=BORD, foreground=TEXTE,
                    arrowcolor=TEXTE, bordercolor=BORD)
        s.map("TCombobox", fieldbackground=[("readonly", CARTE)], foreground=[("readonly", TEXTE)])
        self.option_add("*TCombobox*Listbox.background", CARTE)
        self.option_add("*TCombobox*Listbox.foreground", TEXTE)

    def _carte(self, parent, titre):
        cadre = tk.Frame(parent, bg=CARTE, highlightbackground=BORD, highlightthickness=1)
        cadre.pack(fill="x", pady=(0, 12))
        tk.Label(cadre, text=titre, bg=CARTE, fg=GRIS, font=(POLICE, 10, "bold")).pack(
            anchor="w", padx=14, pady=(10, 4))
        interieur = tk.Frame(cadre, bg=CARTE)
        interieur.pack(fill="x", padx=14, pady=(0, 12))
        return interieur

    def _bouton(self, parent, texte, commande, couleur=BLEU, largeur=None):
        b = tk.Button(parent, text=texte, command=commande, bg=couleur, fg="white",
                      activebackground=couleur, activeforeground="white", relief="flat",
                      font=(POLICE, 10, "bold"), padx=12, pady=5, cursor="hand2", bd=0)
        if largeur:
            b.configure(width=largeur)
        return b

    # ----- en-tête -----
    def _entete(self):
        haut = tk.Frame(self, bg=FOND)
        haut.pack(fill="x", padx=16, pady=12)
        Etoile(haut).pack(side="left")
        tk.Label(haut, text="super afk", bg=FOND, fg=TEXTE,
                 font=(POLICE, 30, "bold")).pack(side="left", padx=16)
        tk.Label(haut, text="F9 = ON/OFF   •   F8 = diagnostic   •   F10 = quitter",
                 bg=FOND, fg=GRIS, font=(POLICE, 10)).pack(side="right")

    # ----- état du bot -----
    def _bloc_etat(self, parent):
        c = self._carte(parent, "ÉTAT DU BOT")
        self.lbl_marche = tk.Label(c, text="", bg=CARTE, font=(POLICE, 18, "bold"))
        self.lbl_marche.grid(row=0, column=0, columnspan=2, sticky="w")
        self.lbl_infos = {}
        for i, (cle, texte) in enumerate([("etat", "État"), ("session", "Session"),
                                          ("capture", "Capture"), ("manette", "Manette")], start=1):
            tk.Label(c, text=texte, bg=CARTE, fg=GRIS, font=(POLICE, 10)).grid(
                row=i, column=0, sticky="w", pady=1)
            lbl = tk.Label(c, text="", bg=CARTE, fg=TEXTE, font=(POLICE, 10, "bold"))
            lbl.grid(row=i, column=1, sticky="w", padx=(12, 0), pady=1)
            self.lbl_infos[cle] = lbl
        boutons = tk.Frame(c, bg=CARTE)
        boutons.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.btn_marche = self._bouton(boutons, "Démarrer", bot.toggle, VERT, largeur=10)
        self.btn_marche.pack(side="left")
        self._bouton(boutons, "Diagnostic", self._diagnostic, BORD).pack(side="left", padx=8)
        self._bouton(boutons, "Quitter", bot.quitter, ROUGE).pack(side="left")

    def _diagnostic(self):
        import threading
        threading.Thread(target=bot.diagnostic, daemon=True).start()

    # ----- paramètres -----
    def _bloc_parametres(self, parent):
        c = self._carte(parent, "PARAMÈTRES")
        actuels = bot.lire_config()
        for i, (cle, texte) in enumerate(LIBELLES_PARAMETRES):
            tk.Label(c, text=texte, bg=CARTE, fg=TEXTE, font=(POLICE, 10)).grid(
                row=i, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=formater(actuels[cle]))
            self.vars_param[cle] = var
            if cle == "TYPE_MANETTE":
                champ = ttk.Combobox(c, textvariable=var, values=["ps4", "xbox"],
                                     state="readonly", width=10)
            else:
                champ = tk.Entry(c, textvariable=var, width=12, bg=FOND, fg=TEXTE,
                                 insertbackground=TEXTE, relief="flat",
                                 highlightbackground=BORD, highlightthickness=1)
            champ.grid(row=i, column=1, sticky="w", padx=(12, 0), pady=3)
        tk.Label(c, text="Le type de manette s'applique au prochain lancement.",
                 bg=CARTE, fg=GRIS, font=(POLICE, 9)).grid(
            row=len(LIBELLES_PARAMETRES), column=0, columnspan=2, sticky="w", pady=(6, 0))
        boutons = tk.Frame(c, bg=CARTE)
        boutons.grid(row=len(LIBELLES_PARAMETRES) + 1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._bouton(boutons, "Enregistrer", self._enregistrer).pack(side="left")
        self._bouton(boutons, "Valeurs par défaut", self._defaut, BORD).pack(side="left", padx=8)

    def _enregistrer(self):
        brut = {cle: var.get() for cle, var in self.vars_param.items()}
        try:
            p = bot.appliquer_parametres(brut)
        except ValueError as e:
            messagebox.showerror("super afk", f"Paramètre invalide :\n{e}", parent=self)
            return
        except OSError as e:
            messagebox.showerror("super afk", f"Impossible d'enregistrer config.json :\n{e}", parent=self)
            return
        for cle, var in self.vars_param.items():
            var.set(formater(p[cle]))
        print("Paramètres enregistrés")
        if p["TYPE_MANETTE"] != bot.MANETTE_CHARGEE:
            messagebox.showinfo("super afk", "Paramètres enregistrés.\n\nRelance super afk pour "
                                f"passer à la manette « {p['TYPE_MANETTE']} ».", parent=self)

    def _defaut(self):
        for cle, var in self.vars_param.items():
            var.set(formater(bot.PARAMETRES_DEFAUT[cle]))
        print("Valeurs par défaut remises dans les champs : clique sur Enregistrer pour les garder")

    # ----- images de référence -----
    def _bloc_images(self, parent):
        c = self._carte(parent, "IMAGES DE RÉFÉRENCE")
        ligne_delai = tk.Frame(c, bg=CARTE)
        ligne_delai.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        tk.Label(ligne_delai, text="Capture après", bg=CARTE, fg=TEXTE, font=(POLICE, 10)).pack(side="left")
        self.var_delai = tk.StringVar(value="3")
        tk.Spinbox(ligne_delai, from_=1, to=30, textvariable=self.var_delai, width=4,
                   bg=FOND, fg=TEXTE, buttonbackground=BORD, relief="flat",
                   insertbackground=TEXTE).pack(side="left", padx=6)
        tk.Label(ligne_delai, text="s (le temps d'afficher l'écran voulu dans Fortnite)",
                 bg=CARTE, fg=GRIS, font=(POLICE, 9)).pack(side="left")

        for i, nom in enumerate(bot.IMAGES, start=1):
            tk.Label(c, text=LIBELLES_IMAGES[nom], bg=CARTE, fg=TEXTE, font=(POLICE, 10),
                     width=20, anchor="w").grid(row=i, column=0, sticky="w", pady=4)
            vignette = tk.Label(c, bg=FOND, width=200, height=44)
            vignette.grid(row=i, column=1, padx=8, pady=4)
            taille = tk.Label(c, text="", bg=CARTE, fg=GRIS, font=(POLICE, 9), width=9)
            taille.grid(row=i, column=2)
            b = self._bouton(c, "Refaire", lambda n=nom: self._refaire(n), BORD)
            b.grid(row=i, column=3, padx=(8, 0))
            self.boutons_refaire.append(b)
            self.vignettes[nom] = (vignette, taille)
        self._maj_vignettes()

    def _maj_vignettes(self):
        for nom, (vignette, taille) in self.vignettes.items():
            img = getattr(bot, f"template_{nom}")
            photo = image_tk(img, 200, 44)
            vignette.configure(image=photo, width=200, height=44)
            vignette.image = photo
            taille.configure(text=f"{img.shape[1]}x{img.shape[0]}")

    def _refaire(self, nom):
        if self.capture_en_cours:
            return
        try:
            delai = int(self.var_delai.get())
            if not 1 <= delai <= 30:
                raise ValueError
        except ValueError:
            messagebox.showerror("super afk", "Le délai doit être un nombre entre 1 et 30.", parent=self)
            return
        self.capture_en_cours = True
        for b in self.boutons_refaire:
            b.configure(state="disabled")
        print(f"Capture de « {nom} » dans {delai} s : affiche cet écran dans Fortnite")
        self.iconify()
        self.compteur = tk.Toplevel(self)
        self.compteur.overrideredirect(True)
        self.compteur.attributes("-topmost", True)
        self.compteur.configure(bg="black")
        self.lbl_compteur = tk.Label(self.compteur, text="", bg="black", fg="white",
                                     font=(POLICE, 14, "bold"), padx=16, pady=8)
        self.lbl_compteur.pack()
        self.compteur.update_idletasks()
        sw = self.compteur.winfo_screenwidth()
        self.compteur.geometry(f"+{(sw - 360) // 2}+4")
        self._decompte(nom, delai)

    def _decompte(self, nom, reste):
        if reste > 0:
            self.lbl_compteur.configure(
                text=f"super afk : capture de « {LIBELLES_IMAGES[nom]} » dans {reste} s")
            self.after(1000, self._decompte, nom, reste - 1)
            return
        self.compteur.destroy()
        self.after(250, self._capturer, nom)   # laisse le compteur disparaître de l'écran

    def _capturer(self, nom):
        try:
            with mss.MSS() as sct:
                frame = bot.capturer(sct)
        except Exception as e:
            self._fin_capture()
            messagebox.showerror("super afk", f"Capture impossible :\n{e}", parent=self)
            return
        SelecteurZone(self, frame, nom, lambda zone: self._zone_choisie(nom, zone))

    def _zone_choisie(self, nom, zone):
        self._fin_capture()
        if zone is None:
            print(f"Capture de « {nom} » annulée")
            return
        try:
            bot.remplacer_template(nom, zone)
        except Exception as e:
            messagebox.showerror("super afk", f"Impossible d'enregistrer l'image :\n{e}", parent=self)
            return
        self._maj_vignettes()
        print("L'ancienne image est gardée dans le dossier anciennes_images")

    def _fin_capture(self):
        self.capture_en_cours = False
        for b in self.boutons_refaire:
            b.configure(state="normal")
        self.deiconify()
        self.lift()

    # ----- journal -----
    def _bloc_journal(self, parent):
        c = self._carte(parent, "JOURNAL")
        self.journal = tk.Text(c, height=9, width=70, bg=FOND, fg=TEXTE, relief="flat",
                               font=("Consolas", 9), state="disabled", wrap="word",
                               highlightthickness=0)
        self.journal.pack(fill="both")

    # ----- mise à jour périodique -----
    def _rafraichir(self):
        morceaux = []
        while True:
            try:
                morceaux.append(_messages.get_nowait())
            except queue.Empty:
                break
        if morceaux:
            self.journal.configure(state="normal")
            self.journal.insert("end", "".join(morceaux))
            lignes = int(self.journal.index("end-1c").split(".")[0])
            if lignes > 500:
                self.journal.delete("1.0", f"{lignes - 500}.0")
            self.journal.see("end")
            self.journal.configure(state="disabled")

        if bot.running:
            self.lbl_marche.configure(text="● EN MARCHE", fg=VERT)
            self.btn_marche.configure(text="Arrêter", bg=ROUGE, activebackground=ROUGE)
        else:
            self.lbl_marche.configure(text="● ARRÊTÉ", fg=ROUGE)
            self.btn_marche.configure(text="Démarrer", bg=VERT, activebackground=VERT)

        self.lbl_infos["etat"].configure(text=bot.etat)
        if bot.sequence_en_cours:
            session = "séquence de fin"
        elif bot.session_active:
            reste = max(0, int(bot.session_fin - time.time()))
            session = f"en cours, {reste // 60}:{reste % 60:02d} restantes"
        else:
            session = "aucune"
        self.lbl_infos["session"].configure(text=session)
        self.lbl_infos["capture"].configure(text=bot._mode_capture or "—")
        self.lbl_infos["manette"].configure(text=bot.MANETTE_CHARGEE)
        self.after(200, self._rafraichir)


if __name__ == "__main__":
    Interface().mainloop()
