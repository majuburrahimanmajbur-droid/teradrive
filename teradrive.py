"""TeraDrive - log in to TeraBox, Google Drive and MEGA and use them as ONE virtual drive.

How it works: rclone talks to the clouds, rclone's "union" remote merges them,
WinFsp shows the result as a drive letter, and a big cache on your real SSD
makes repeat reads fast. Windows only.
"""
import glob
import io
import json
import os
import shutil
import string
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile
import ctypes
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "TeraDrive"
IS_WIN = os.name == "nt"
APPDATA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
BIN_DIR = os.path.join(APPDATA, "bin")
CONFIG = os.path.join(APPDATA, "config.json")
RCLONE_CONF = os.path.join(APPDATA, "rclone.conf")
LOG = os.path.join(APPDATA, "rclone.log")
os.makedirs(BIN_DIR, exist_ok=True)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if IS_WIN else 0
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10) if IS_WIN else 0

ORDER = ["terabox", "gdrive", "mega"]
PROVIDERS = {
    "terabox": {"label": "TeraBox", "color": "#3A9BFF"},
    "gdrive": {"label": "Google Drive", "color": "#34A853"},
    "mega": {"label": "MEGA", "color": "#D9272E"},
}
UNION = "teradrive"
POLICIES = {"Most free space first": "mfs", "Fill the first account first": "ff"}

BG = "#161A24"
PANEL = "#1E2433"
PANEL2 = "#273049"
LINE = "#2D3550"
TEXT = "#E7EBF4"
MUTED = "#8C96B1"
ACCENT = "#6C8CFF"
OK = "#4CC38A"
WARN = "#F0B44C"
BAD = "#F2706B"
FONT = "Segoe UI"

DEFAULTS = {
    "rclone_path": "",
    "drive": "T:",
    "cache_dir": os.path.join(APPDATA, "cache"),
    "cache_gb": 100,
    "policy": "Most free space first",
    "network_mode": False,
}


# ---------- helpers ----------

def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    try:
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def fmt(n):
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def find_rclone(cfg):
    p = cfg.get("rclone_path")
    if p and os.path.isfile(p):
        return p
    mine = os.path.join(BIN_DIR, "rclone.exe")
    if os.path.isfile(mine):
        return mine
    exe_dir = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    near = os.path.join(exe_dir, "rclone.exe")
    if os.path.isfile(near):
        return near
    found = shutil.which("rclone")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    for pattern in (
        os.path.join(local, "Microsoft", "WinGet", "Links", "rclone.exe"),
        os.path.join(local, "Microsoft", "WinGet", "Packages", "Rclone.Rclone*", "**", "rclone.exe"),
    ):
        hits = glob.glob(pattern, recursive=True)
        if hits:
            return hits[0]
    return None


def winfsp_installed():
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base and os.path.isdir(os.path.join(base, "WinFsp")):
            return True
    return False


def free_letters():
    if not IS_WIN:
        return ["T:"]
    used = ctypes.windll.kernel32.GetLogicalDrives()
    pref = "TUVWXYZSRQPONMLKJIHGFED"
    return [f"{c}:" for c in pref if not used & (1 << (ord(c) - 65))]


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


class RC:
    """Thin wrapper around rclone.exe using TeraDrive's own config file."""

    def __init__(self, exe):
        self.exe = exe

    def base(self):
        return [self.exe, "--config", RCLONE_CONF]

    def run(self, args, timeout=120, input_text=None):
        try:
            p = subprocess.run(
                self.base() + args, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout, input=input_text, creationflags=NO_WINDOW,
            )
            return p.returncode, p.stdout, p.stderr
        except Exception as exc:  # timeout, missing exe...
            return 1, "", str(exc)

    def remotes(self):
        code, out, _ = self.run(["listremotes"], timeout=20)
        if code != 0:
            return set()
        return {line.strip().rstrip(":") for line in out.splitlines() if line.strip()}

    def about(self, name):
        code, out, _ = self.run(["about", f"{name}:", "--json"], timeout=60)
        if code != 0:
            return {}
        try:
            return json.loads(out)
        except ValueError:
            return {}

    def has_backend(self, name):
        _code, out, _ = self.run(["help", "backends"], timeout=20)
        return name in out.lower()


# ---------- small UI pieces ----------

def make_button(parent, text, cmd, kind="normal"):
    colors = {"primary": ACCENT, "normal": PANEL2, "danger": "#46283A"}
    bg = colors[kind]
    return tk.Button(
        parent, text=text, command=cmd, bg=bg, fg=TEXT, activebackground=mix(bg, "#FFFFFF", 0.15),
        activeforeground=TEXT, relief="flat", bd=0, padx=14, pady=7, font=(FONT, 10), cursor="hand2",
        disabledforeground=MUTED,
    )


def make_label(parent, text, color=TEXT, size=10, bold=False, **kw):
    return tk.Label(parent, text=text, bg=kw.pop("bg", PANEL), fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), **kw)


def make_entry(parent, width=30, show=None):
    return tk.Entry(parent, width=width, bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat",
                    highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
                    font=(FONT, 10), show=show)


class LoginDialog(tk.Toplevel):
    """Email + password box (used for MEGA)."""

    def __init__(self, master, title):
        super().__init__(master)
        self.title(title)
        self.configure(bg=PANEL, padx=20, pady=18)
        self.resizable(False, False)
        self.result = None
        make_label(self, "Email").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.e_mail = make_entry(self, 36)
        self.e_mail.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 12), ipady=4)
        make_label(self, "Password").grid(row=2, column=0, sticky="w", pady=(0, 4))
        self.e_pass = make_entry(self, 36, show="*")
        self.e_pass.grid(row=3, column=0, columnspan=2, sticky="we", pady=(0, 14), ipady=4)
        make_button(self, "Log in", self.ok, "primary").grid(row=4, column=0, sticky="we", padx=(0, 6))
        make_button(self, "Cancel", self.destroy).grid(row=4, column=1, sticky="we")
        self.bind("<Return>", lambda _e: self.ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.transient(master)
        self.grab_set()
        self.e_mail.focus_set()
        master.wait_window(self)

    def ok(self):
        mail, pw = self.e_mail.get().strip(), self.e_pass.get()
        if not mail or not pw:
            return
        self.result = (mail, pw)
        self.destroy()


# ---------- main app ----------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("780x860")
        self.minsize(700, 720)
        self.configure(bg=BG)
        self.cfg = load_cfg()
        self.proc = None
        self.expect_mount = False
        self.accounts = {}  # key -> dict from `rclone about` ({} = connected, size unknown)
        self.rows = {}
        self.cache_bytes = 0
        self.warm = {"running": False, "files": 0, "bytes": 0, "cancel": False}
        self.last_log = ""
        self.build_ui()
        self.refresh_requirements()
        self.refresh_accounts()
        threading.Thread(target=self.cache_size_loop, daemon=True).start()
        self.after(1500, self.tick)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ----- layout -----
    def card(self, parent, pady=(0, 12)):
        f = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        f.pack(fill="x", pady=pady)
        inner = tk.Frame(f, bg=PANEL)
        inner.pack(fill="x", padx=16, pady=14)
        return inner

    def build_ui(self):
        root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True, padx=20, pady=18)

        make_label(root, "TeraDrive", TEXT, 22, True, bg=BG).pack(anchor="w")
        make_label(root, "Log in once. Your free cloud storage becomes one drive.", MUTED, 10, bg=BG).pack(anchor="w", pady=(0, 12))

        self.bar = tk.Canvas(root, height=26, bg=BG, highlightthickness=0)
        self.bar.pack(fill="x")
        self.bar.bind("<Configure>", lambda _e: self.draw_bar())
        self.summary = make_label(root, "No storage connected yet", MUTED, 10, bg=BG)
        self.summary.pack(anchor="w", pady=(6, 14))

        acc = self.card(root)
        make_label(acc, "Accounts", TEXT, 12, True).pack(anchor="w", pady=(0, 8))
        for key in ORDER:
            row = tk.Frame(acc, bg=PANEL)
            row.pack(fill="x", pady=5)
            dot = tk.Canvas(row, width=14, height=14, bg=PANEL, highlightthickness=0)
            dot.create_oval(2, 2, 12, 12, fill=PROVIDERS[key]["color"], outline="")
            dot.pack(side="left", padx=(0, 10))
            name = make_label(row, PROVIDERS[key]["label"], TEXT, 11, True, width=13, anchor="w")
            name.pack(side="left")
            status = make_label(row, "Not connected", MUTED, 10, anchor="w")
            status.pack(side="left", fill="x", expand=True)
            out_btn = make_button(row, "Log out", lambda k=key: self.logout(k), "danger")
            in_btn = make_button(row, "Log in", lambda k=key: self.login(k), "primary")
            in_btn.pack(side="right")
            self.rows[key] = {"status": status, "in": in_btn, "out": out_btn}

        drv = self.card(root)
        make_label(drv, "Drive", TEXT, 12, True).grid(row=0, column=0, sticky="w", pady=(0, 8))
        grid = tk.Frame(drv, bg=PANEL)
        grid.grid(row=1, column=0, sticky="we")
        drv.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        make_label(grid, "Drive letter").grid(row=0, column=0, sticky="w", pady=4)
        letters = free_letters()
        if self.cfg["drive"] not in letters:
            letters.insert(0, self.cfg["drive"])
        self.v_drive = tk.StringVar(value=self.cfg["drive"])
        ttk.Combobox(grid, textvariable=self.v_drive, values=letters, width=6, state="readonly").grid(row=0, column=1, sticky="w", padx=10)

        make_label(grid, "Cache folder").grid(row=1, column=0, sticky="w", pady=4)
        self.e_cache = make_entry(grid, 40)
        self.e_cache.insert(0, self.cfg["cache_dir"])
        self.e_cache.grid(row=1, column=1, sticky="we", padx=10, ipady=3)
        make_button(grid, "Browse", self.pick_cache).grid(row=1, column=2)

        make_label(grid, "Cache size (GB)").grid(row=2, column=0, sticky="w", pady=4)
        self.v_gb = tk.IntVar(value=int(self.cfg["cache_gb"]))
        tk.Spinbox(grid, from_=5, to=4000, increment=5, textvariable=self.v_gb, width=8, bg=PANEL2, fg=TEXT,
                   buttonbackground=PANEL2, relief="flat", insertbackground=TEXT, font=(FONT, 10)).grid(row=2, column=1, sticky="w", padx=10)

        make_label(grid, "New files go to").grid(row=3, column=0, sticky="w", pady=4)
        self.v_policy = tk.StringVar(value=self.cfg["policy"])
        ttk.Combobox(grid, textvariable=self.v_policy, values=list(POLICIES), width=28, state="readonly").grid(row=3, column=1, sticky="w", padx=10)

        self.v_net = tk.BooleanVar(value=bool(self.cfg["network_mode"]))
        tk.Checkbutton(grid, text="Network drive mode (try this if a game freezes)", variable=self.v_net, bg=PANEL, fg=TEXT,
                       activebackground=PANEL, activeforeground=TEXT, selectcolor=PANEL2, font=(FONT, 10)).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        act = tk.Frame(drv, bg=PANEL)
        act.grid(row=2, column=0, sticky="we", pady=(14, 0))
        self.b_mount = make_button(act, "Mount drive", self.mount, "primary")
        self.b_mount.pack(side="left")
        self.b_unmount = make_button(act, "Unmount", self.unmount)
        self.b_unmount.pack(side="left", padx=8)
        self.b_open = make_button(act, "Open drive", self.open_drive)
        self.b_open.pack(side="left")
        self.b_warm = make_button(act, "Pre-load a folder", self.warm_cache)
        self.b_warm.pack(side="right")

        self.status = make_label(drv, "Not mounted", MUTED, 11, True)
        self.status.grid(row=3, column=0, sticky="w", pady=(12, 0))
        self.info = make_label(drv, "", MUTED, 10, anchor="w", justify="left", wraplength=680)
        self.info.grid(row=4, column=0, sticky="w")

        req = self.card(root)
        make_label(req, "Setup", TEXT, 12, True).pack(anchor="w", pady=(0, 6))
        r1 = tk.Frame(req, bg=PANEL)
        r1.pack(fill="x", pady=3)
        self.req_rclone = make_label(r1, "rclone: checking", MUTED, 10, anchor="w")
        self.req_rclone.pack(side="left", fill="x", expand=True)
        make_button(r1, "Use my rclone.exe", self.pick_rclone).pack(side="right", padx=(8, 0))
        self.b_get_rclone = make_button(r1, "Get rclone", self.download_rclone)
        self.b_get_rclone.pack(side="right")
        r2 = tk.Frame(req, bg=PANEL)
        r2.pack(fill="x", pady=3)
        self.req_winfsp = make_label(r2, "WinFsp: checking", MUTED, 10, anchor="w")
        self.req_winfsp.pack(side="left", fill="x", expand=True)
        make_button(r2, "Install WinFsp", self.install_winfsp).pack(side="right")

        logf = self.card(root, pady=(0, 0))
        make_label(logf, "Activity", TEXT, 12, True).pack(anchor="w", pady=(0, 6))
        self.log = tk.Text(logf, height=7, bg=PANEL2, fg=MUTED, relief="flat", font=("Consolas", 9), wrap="word", state="disabled")
        self.log.pack(fill="x")

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=TEXT, arrowcolor=TEXT,
                        bordercolor=LINE, lightcolor=PANEL2, darkcolor=PANEL2)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL2)], foreground=[("readonly", TEXT)])
        self.update_buttons(False)

    # ----- small UI utils -----
    def ui(self, fn, *args):
        self.after(0, lambda: fn(*args))

    def set_status(self, text, color):
        self.status.config(text=text, fg=color)

    def note(self, text, color=MUTED):
        self.info.config(text=text, fg=color)

    def update_buttons(self, mounted):
        self.b_mount.config(state="disabled" if mounted else "normal")
        self.b_unmount.config(state="normal" if mounted else "disabled")
        self.b_open.config(state="normal" if mounted else "disabled")
        self.b_warm.config(state="normal" if mounted else "disabled")

    def pick_cache(self):
        d = filedialog.askdirectory(title="Pick a cache folder on your fastest SSD")
        if d:
            self.e_cache.delete(0, "end")
            self.e_cache.insert(0, os.path.normpath(d))

    def collect_cfg(self):
        self.cfg["drive"] = self.v_drive.get()
        self.cfg["cache_dir"] = self.e_cache.get().strip() or DEFAULTS["cache_dir"]
        try:
            self.cfg["cache_gb"] = max(5, int(self.v_gb.get()))
        except (tk.TclError, ValueError):
            self.cfg["cache_gb"] = DEFAULTS["cache_gb"]
        self.cfg["policy"] = self.v_policy.get()
        self.cfg["network_mode"] = bool(self.v_net.get())
        save_cfg(self.cfg)

    # ----- requirements -----
    def get_rc(self):
        exe = find_rclone(self.cfg)
        return RC(exe) if exe else None

    def need_rclone(self):
        rc = self.get_rc()
        if not rc:
            messagebox.showinfo(APP_NAME, "rclone is missing. Click 'Get rclone' in the Setup box first.")
        return rc

    def refresh_requirements(self):
        exe = find_rclone(self.cfg)
        if exe:
            self.req_rclone.config(text=f"rclone: found ({exe})", fg=OK)
        else:
            self.req_rclone.config(text="rclone: not found", fg=BAD)
        if winfsp_installed():
            self.req_winfsp.config(text="WinFsp: installed", fg=OK)
        else:
            self.req_winfsp.config(text="WinFsp: not installed (needed for the drive letter)", fg=BAD)

    def pick_rclone(self):
        p = filedialog.askopenfilename(title="Pick rclone.exe", filetypes=[("rclone", "rclone.exe"), ("Programs", "*.exe")])
        if p:
            self.cfg["rclone_path"] = p
            save_cfg(self.cfg)
            self.refresh_requirements()
            self.refresh_accounts()

    def download_rclone(self):
        self.b_get_rclone.config(state="disabled", text="Downloading")

        def work():
            try:
                url = "https://downloads.rclone.org/rclone-current-windows-amd64.zip"
                data = urllib.request.urlopen(url, timeout=120).read()
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for name in z.namelist():
                        if name.lower().endswith("/rclone.exe"):
                            with z.open(name) as src, open(os.path.join(BIN_DIR, "rclone.exe"), "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            break
                    else:
                        raise RuntimeError("rclone.exe was not in the download")
                self.ui(self.note, "rclone downloaded.", OK)
            except Exception as exc:
                self.ui(messagebox.showerror, APP_NAME, f"Could not download rclone:\n{exc}\n\nDownload it from rclone.org and use 'Use my rclone.exe'.")
            self.ui(self.b_get_rclone.config, {"state": "normal", "text": "Get rclone"})
            self.ui(self.refresh_requirements)
            self.ui(self.refresh_accounts)

        threading.Thread(target=work, daemon=True).start()

    def install_winfsp(self):
        def work():
            try:
                p = subprocess.Popen(["winget", "install", "-e", "--id", "WinFsp.WinFsp"], creationflags=NEW_CONSOLE)
                p.wait()
            except (FileNotFoundError, OSError):
                webbrowser.open("https://winfsp.dev/rel/")
            self.ui(self.refresh_requirements)

        threading.Thread(target=work, daemon=True).start()

    # ----- accounts -----
    def run_console(self, args):
        """Run an rclone command in a visible console (for logins that need a browser or questions)."""
        rc = self.need_rclone()
        if not rc:
            return
        bat = os.path.join(APPDATA, "console_task.bat")
        cmd = subprocess.list2cmdline(rc.base() + args)
        with open(bat, "w", encoding="utf-8") as f:
            f.write("@echo off\r\n" + cmd + "\r\necho.\r\necho Done. You can close this window.\r\npause\r\n")

        def work():
            try:
                subprocess.Popen(["cmd", "/c", bat], creationflags=NEW_CONSOLE).wait()
            except OSError as exc:
                self.ui(messagebox.showerror, APP_NAME, str(exc))
            self.ui(self.refresh_accounts)

        threading.Thread(target=work, daemon=True).start()

    def login(self, key):
        rc = self.need_rclone()
        if not rc:
            return
        if key == "gdrive":
            messagebox.showinfo(APP_NAME, "A console window opens and your browser asks you to sign in to Google. Allow access, then come back here.")
            self.run_console(["config", "create", "gdrive", "drive", "scope=drive"])
        elif key == "mega":
            dlg = LoginDialog(self, "Log in to MEGA")
            if dlg.result:
                self.mega_login(rc, *dlg.result)
        else:
            self.terabox_login(rc)

    def mega_login(self, rc, mail, password):
        self.note("Logging in to MEGA...", WARN)

        def work():
            code, out, err = rc.run(["obscure", "-"], timeout=20, input_text=password)
            if code != 0:
                self.ui(self.note, f"Could not prepare the password: {err.strip()}", BAD)
                return
            rc.run(["config", "delete", "mega"], timeout=20)
            code, _o, err = rc.run(["config", "create", "mega", "mega", f"user={mail}", f"pass={out.strip()}"], timeout=60)
            if code == 0:
                code, _o, err = rc.run(["lsd", "mega:", "--max-depth", "1"], timeout=90)
            if code != 0:
                rc.run(["config", "delete", "mega"], timeout=20)
                self.ui(self.note, "MEGA login failed. Check email and password (accounts with 2FA are not supported here).", BAD)
            else:
                self.ui(self.note, "MEGA connected.", OK)
            self.ui(self.refresh_accounts)

        threading.Thread(target=work, daemon=True).start()

    def terabox_login(self, rc):
        if rc.has_backend("terabox"):
            msg = ("This rclone supports TeraBox.\n\nA console window will open. Choose 'n' (new remote), name it "
                   "terabox, pick TeraBox as the storage type and follow the questions.")
        else:
            msg = ("This rclone has no TeraBox support. TeraBox has no official connection method, so you need a "
                   "TeraBox-enabled rclone build (use 'Use my rclone.exe') or a bridge that shows TeraBox as a "
                   "normal remote, for example WebDAV.\n\nIf you have one, the console window will let you create "
                   "a remote named terabox. Open it now?")
        if messagebox.askyesno(APP_NAME, msg):
            self.run_console(["config"])

    def logout(self, key):
        rc = self.need_rclone()
        if not rc:
            return
        if not messagebox.askyesno(APP_NAME, f"Log out of {PROVIDERS[key]['label']}? Your files stay in the cloud."):
            return

        def work():
            rc.run(["config", "delete", key], timeout=20)
            self.ui(self.refresh_accounts)

        threading.Thread(target=work, daemon=True).start()

    def refresh_accounts(self):
        def work():
            rc = self.get_rc()
            result = {}
            if rc:
                names = rc.remotes()
                for key in ORDER:
                    if key in names:
                        result[key] = rc.about(key)
            self.ui(self.apply_accounts, result)

        threading.Thread(target=work, daemon=True).start()

    def apply_accounts(self, result):
        self.accounts = result
        for key in ORDER:
            row = self.rows[key]
            if key in result:
                info = result[key]
                if info.get("total"):
                    text = f"Connected. {fmt(info.get('free'))} free of {fmt(info['total'])}"
                else:
                    text = "Connected (size unknown)"
                row["status"].config(text=text, fg=OK)
                row["in"].pack_forget()
                row["out"].pack(side="right")
            else:
                row["status"].config(text="Not connected", fg=MUTED)
                row["out"].pack_forget()
                row["in"].pack(side="right")
        self.draw_bar()

    def draw_bar(self):
        c = self.bar
        c.delete("all")
        w = max(c.winfo_width(), 200)
        h = 26
        items = [(k, v) for k, v in self.accounts.items() if v.get("total")]
        if not items:
            c.create_rectangle(0, 0, w, h, fill=PANEL, outline=LINE)
            self.summary.config(text="No storage connected yet" if not self.accounts else "Connected, but sizes are unknown")
            return
        total = sum(v["total"] for _k, v in items)
        free = sum((v.get("free") or 0) for _k, v in items)
        widths = [max(v["total"] / total, 0.05) for _k, v in items]
        norm = sum(widths)
        x = 0
        for (k, v), wf in zip(items, widths):
            sw = w * wf / norm
            base = PROVIDERS[k]["color"]
            used = min(max(v.get("used") or 0, 0), v["total"])
            c.create_rectangle(x, 0, x + sw, h, fill=base, outline=BG, width=2)
            c.create_rectangle(x, 0, x + sw * (used / v["total"]), h, fill=mix(base, BG, 0.65), outline="")
            x += sw
        extra = len(self.accounts) - len(items)
        tail = f" (+{extra} without size info)" if extra else ""
        self.summary.config(text=f"Combined: {fmt(total)} total, {fmt(free)} free{tail}")

    # ----- mount -----
    def mount(self):
        if self.proc and self.proc.poll() is None:
            return
        rc = self.need_rclone()
        if not rc:
            return
        if not winfsp_installed():
            messagebox.showwarning(APP_NAME, "WinFsp is not installed. Click 'Install WinFsp' in the Setup box.")
            return
        connected = [k for k in ORDER if k in self.accounts]
        if not connected:
            messagebox.showinfo(APP_NAME, "Log in to at least one account first.")
            return
        self.collect_cfg()
        drive = self.cfg["drive"]
        if os.path.exists(drive + "\\"):
            messagebox.showwarning(APP_NAME, f"{drive} is already in use. Pick another drive letter.")
            return
        cache = self.cfg["cache_dir"]
        try:
            os.makedirs(cache, exist_ok=True)
            free = shutil.disk_usage(cache).free
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Cannot use the cache folder:\n{exc}")
            return
        if self.cfg["cache_gb"] * 1024 ** 3 > free and not messagebox.askyesno(
                APP_NAME, f"The cache ({self.cfg['cache_gb']} GB) is bigger than the free space on that disk ({fmt(free)}). Continue anyway?"):
            return
        self.b_mount.config(state="disabled")
        self.set_status("Starting...", WARN)
        threading.Thread(target=self.mount_worker, args=(rc, connected), daemon=True).start()

    def mount_worker(self, rc, connected):
        policy = POLICIES.get(self.cfg["policy"], "mfs")
        rc.run(["config", "delete", UNION], timeout=20)
        code, _o, err = rc.run(["config", "create", UNION, "union", "upstreams=" + " ".join(f"{k}:" for k in connected),
                                f"create_policy={policy}", "action_policy=epall", "search_policy=ff"], timeout=30)
        if code != 0:
            self.ui(self.note, f"Could not combine the accounts: {err.strip()[:300]}", BAD)
            self.ui(self.update_buttons, False)
            self.ui(self.set_status, "Not mounted", MUTED)
            return
        open(LOG, "w").close()
        cmd = rc.base() + [
            "mount", f"{UNION}:", self.cfg["drive"],
            "--vfs-cache-mode", "full",
            "--cache-dir", self.cfg["cache_dir"],
            "--vfs-cache-max-size", f"{self.cfg['cache_gb']}G",
            "--vfs-cache-max-age", "8760h",
            "--vfs-cache-min-free-space", "10G",
            "--vfs-read-ahead", "512M",
            "--vfs-read-chunk-size", "64M",
            "--vfs-read-chunk-size-limit", "off",
            "--buffer-size", "32M",
            "--dir-cache-time", "24h",
            "--volname", "TeraDrive",
            "--log-file", LOG, "--log-level", "INFO",
        ]
        if self.cfg["network_mode"]:
            cmd.append("--network-mode")
        try:
            self.proc = subprocess.Popen(cmd, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.expect_mount = True
            self.ui(self.note, "Combined: " + ", ".join(PROVIDERS[k]["label"] for k in connected), MUTED)
        except OSError as exc:
            self.ui(self.note, f"Could not start rclone: {exc}", BAD)
            self.ui(self.update_buttons, False)

    def unmount(self):
        self.expect_mount = False
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.set_status("Not mounted", MUTED)
        self.update_buttons(False)

    def open_drive(self):
        drive = self.cfg["drive"] + "\\"
        if os.path.exists(drive) and IS_WIN:
            os.startfile(drive)  # noqa: S606

    # ----- warm cache -----
    def warm_cache(self):
        if self.warm["running"]:
            self.warm["cancel"] = True
            return
        drive = self.cfg["drive"]
        folder = filedialog.askdirectory(initialdir=drive + "\\", title="Pick a game folder on the virtual drive")
        if not folder:
            return
        if not os.path.abspath(folder).upper().startswith(drive.upper()):
            messagebox.showinfo(APP_NAME, f"Pick a folder inside {drive}")
            return
        self.warm.update(running=True, files=0, bytes=0, cancel=False)
        self.b_warm.config(text="Stop pre-loading")

        def work():
            for root, _d, files in os.walk(folder):
                for name in files:
                    if self.warm["cancel"]:
                        break
                    try:
                        with open(os.path.join(root, name), "rb") as f:
                            while True:
                                chunk = f.read(4 * 1024 * 1024)
                                if not chunk or self.warm["cancel"]:
                                    break
                                self.warm["bytes"] += len(chunk)
                    except OSError:
                        pass
                    self.warm["files"] += 1
                if self.warm["cancel"]:
                    break
            self.warm["running"] = False
            self.ui(self.b_warm.config, {"text": "Pre-load a folder"})

        threading.Thread(target=work, daemon=True).start()

    # ----- background upkeep -----
    def cache_size_loop(self):
        while True:
            path = self.cfg.get("cache_dir", "")
            if path and os.path.isdir(path):
                self.cache_bytes = dir_size(path)
            time.sleep(60)

    def tick(self):
        running = self.proc is not None and self.proc.poll() is None
        drive = self.cfg.get("drive", "T:")
        if running:
            if os.path.exists(drive + "\\"):
                self.set_status(f"Mounted as {drive}", OK)
            else:
                self.set_status("Starting...", WARN)
            self.update_buttons(True)
        else:
            if self.proc is not None:
                self.proc = None
                if self.expect_mount:
                    self.expect_mount = False
                    self.note("The drive stopped. See Activity below for the reason.", BAD)
            self.set_status("Not mounted", MUTED)
            self.update_buttons(False)
        if running:
            extra = f"Cache: {fmt(self.cache_bytes)} of {self.cfg['cache_gb']} GB used."
            if self.warm["running"] or self.warm["files"]:
                extra += f" Pre-loaded {self.warm['files']} files ({fmt(self.warm['bytes'])})."
            if not self.info.cget("text").startswith("Could not"):
                self.note(extra, MUTED)
        self.update_log()
        self.after(2000, self.tick)

    def update_log(self):
        try:
            with open(LOG, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 4000))
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            return
        if text == self.last_log:
            return
        self.last_log = text
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.config(state="disabled")

    def on_close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno(APP_NAME, "The drive is mounted. Unmount and quit?"):
                return
            self.unmount()
        self.destroy()


if __name__ == "__main__":
    if not IS_WIN:
        print("TeraDrive needs Windows (WinFsp). The window will open but mounting will not work here.")
    App().mainloop()
