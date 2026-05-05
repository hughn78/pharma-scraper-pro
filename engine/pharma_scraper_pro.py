#!/usr/bin/env python3
"""
Pharma Scraper Pro — Unified Tkinter GUI
========================================
All-in-one desktop application for:
  1. Multi-site scraping (Shopify JSON + HTML fallback)
  2. Canonical product deduplication
  3. FOS stock report enrichment
  4. Cross-domain barcode merging
  5. Price analysis with size-mismatch detection
  6. Multi-sheet Excel export (Canonical, Source, Price, Shopify, eBay)

Usage:
  python3 pharma_scraper_pro.py

Dependencies (install once):
  pip install requests beautifulsoup4 pandas openpyxl thefuzz lxml
"""

import json, os, sys, re, time, queue, threading, logging, webbrowser, subprocess
from datetime import datetime
from pathlib import Path
from tkinter import (
    Tk, ttk, Frame, Label, Button, Entry, Checkbutton, BooleanVar,
    StringVar, IntVar, DoubleVar, Text, filedialog, messagebox, Menu,
    PanedWindow, HORIZONTAL, VERTICAL, BOTTOM, X, Y, BOTH, LEFT, RIGHT, TOP,
    W, E, N, S, END, NORMAL, DISABLED, Toplevel,
)
from tkinter.scrolledtext import ScrolledText

# ── Ensure engine module is importable ────────────────────────────
ENGINE_DIR = Path(__file__).parent.resolve()
ROOT_DIR = ENGINE_DIR.parent
sys.path.insert(0, str(ENGINE_DIR))

try:
    import core
except ImportError as e:
    print(f"Failed to import engine/core.py: {e}")
    sys.exit(1)

# ── Logging ─────────────────────────────────────────────────────────
LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

class QueueHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q
    def emit(self, record):
        self.q.put({"level": record.levelname, "message": self.format(record), "time": datetime.now().isoformat()})

logger = logging.getLogger("pharma_pro_gui")
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")

# ── GUI ───────────────────────────────────────────────────────────
class PharmaScraperProGUI:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Pharma Scraper Pro v2.0 — Canonical Product Database")
        self.root.geometry("1500x950")
        self.root.minsize(1300, 800)

        self.config = core.load_config()
        self.db_path = Path(self.config.get("db_path", str(core.DB_PATH)))
        self.export_dir = Path(self.config.get("export_dir", str(core.EXPORT_DIR)))
        self.sites = [s.copy() for s in core.DEFAULT_SITES]
        for s in self.sites:
            s["status"] = "idle"
            s["products"] = 0
            s["barcodes"] = 0

        self.msg_queue = queue.Queue()
        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = None

        # Setup logging to GUI
        qh = QueueHandler(self.log_queue)
        qh.setFormatter(fmt)
        logger.addHandler(qh)
        fh = logging.FileHandler(LOG_DIR / f"gui_{datetime.now():%Y%m%d_%H%M%S}.log")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        self.build_menu()
        self.build_notebook()
        self.build_status_bar()
        self.poll_queues()
        self.ensure_db()
        self.refresh_stats()

    # ── Menu ──────────────────────────────────────────────────────
    def build_menu(self):
        menubar = Menu(self.root)
        file_menu = Menu(menubar, tearoff=0)
        file_menu.add_command(label="Load Config", command=self.load_config_dialog)
        file_menu.add_command(label="Save Config", command=self.save_config)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        tools_menu = Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Check DB", command=self.check_db)
        tools_menu.add_command(label="Clear DB (destructive)", command=self.clear_db_dialog)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menubar)

    # ── Notebook / Tabs ───────────────────────────────────────────
    def build_notebook(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=BOTH, expand=True, padx=5, pady=5)
        self.build_dashboard_tab()
        self.build_sites_tab()
        self.build_scrape_tab()
        self.build_canonical_tab()
        self.build_enrich_tab()
        self.build_price_tab()
        self.build_export_tab()
        self.build_settings_tab()
        self.build_logs_tab()

    # ── Dashboard ─────────────────────────────────────────────────
    def build_dashboard_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="📊 Dashboard")

        stats_frame = ttk.Frame(frame)
        stats_frame.pack(fill=X, padx=10, pady=10)
        self.stat_labels = {}
        for label_text, default, color in [
            ("Sites Enabled", "0", "#3498db"), ("Total Scraped", "0", "#2ecc71"),
            ("With Barcodes", "0", "#9b59b6"), ("Canonical Products", "0", "#e74c3c"),
            ("FOS Matches", "0", "#f39c12"),
        ]:
            card = ttk.LabelFrame(stats_frame, text=label_text, padding=10)
            card.pack(side=LEFT, fill=X, expand=True, padx=5)
            lbl = Label(card, text=default, font=("Helvetica", 22, "bold"), fg=color)
            lbl.pack()
            self.stat_labels[label_text] = lbl

        paned = PanedWindow(frame, orient=VERTICAL)
        paned.pack(fill=BOTH, expand=True, padx=10, pady=5)

        actions = ttk.LabelFrame(paned, text="Quick Actions", padding=10)
        paned.add(actions, height=100)
        btn_frame = ttk.Frame(actions)
        btn_frame.pack(fill=X)
        ttk.Button(btn_frame, text="▶ Scrape All", command=self.start_scrape).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="🔗 Canonicalise", command=self.start_canonical).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="✨ Enrich FOS", command=self.start_enrich).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="📈 Price Analysis", command=self.start_price).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="💾 Export Workbook", command=self.start_export).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="🔄 Refresh", command=self.refresh_stats).pack(side=LEFT, padx=5)

        activity = ttk.LabelFrame(paned, text="Activity Log", padding=5)
        paned.add(activity, height=200)
        self.activity_text = ScrolledText(activity, height=8, wrap="word", state=DISABLED)
        self.activity_text.pack(fill=BOTH, expand=True)
        for tag, fg in [("info","#2c3e50"),("success","#27ae60"),("warning","#f39c12"),("error","#c0392b")]:
            self.activity_text.tag_config(tag, foreground=fg)

    # ── Sites ──────────────────────────────────────────────────────
    def build_sites_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="🌐 Sites")

        toolbar = ttk.Frame(frame)
        toolbar.pack(fill=X, padx=5, pady=5)
        ttk.Button(toolbar, text="Select All", command=self.select_all_sites).pack(side=LEFT, padx=2)
        ttk.Button(toolbar, text="Deselect All", command=self.deselect_all_sites).pack(side=LEFT, padx=2)
        ttk.Button(toolbar, text="Load Custom List", command=self.load_custom_sites).pack(side=LEFT, padx=5)

        cols = ("rank", "enabled", "name", "domain", "type", "status", "products", "barcodes", "difficulty")
        self.site_tree = ttk.Treeview(frame, columns=cols, show="headings", height=25)
        widths = [("rank",40),("enabled",60),("name",180),("domain",200),("type",160),("status",80),("products",70),("barcodes",70),("difficulty",90)]
        for col, w in widths:
            self.site_tree.heading(col, text=col.replace("_"," ").title())
            self.site_tree.column(col, width=w, anchor="center" if col in ("rank","enabled","products","barcodes","difficulty") else "w")
        vsb = ttk.Scrollbar(frame, orient=VERTICAL, command=self.site_tree.yview)
        self.site_tree.configure(yscrollcommand=vsb.set)
        self.site_tree.pack(side=LEFT, fill=BOTH, expand=True)
        vsb.pack(side=RIGHT, fill=Y)
        self.site_tree.bind("<Double-1>", self.toggle_site)
        self.populate_sites()

    # ── Scrape ──────────────────────────────────────────────────────
    def build_scrape_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="🔍 Scrape")

        ctrl = ttk.LabelFrame(frame, text="Scrape Controls", padding=10)
        ctrl.pack(fill=X, padx=10, pady=5)
        self.scrape_btn = ttk.Button(ctrl, text="▶ Start Scrape", command=self.start_scrape)
        self.scrape_btn.pack(side=LEFT, padx=5)
        self.stop_btn = ttk.Button(ctrl, text="⏹ Stop", command=self.stop_worker, state=DISABLED)
        self.stop_btn.pack(side=LEFT, padx=5)
        ttk.Separator(ctrl, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=15)
        ttk.Label(ctrl, text="Progress:").pack(side=LEFT, padx=5)
        self.scrape_progress = ttk.Progressbar(ctrl, length=300, mode="determinate")
        self.scrape_progress.pack(side=LEFT, padx=5)
        self.scrape_status_lbl = ttk.Label(ctrl, text="Ready")
        self.scrape_status_lbl.pack(side=LEFT, padx=10)

        detail = ttk.LabelFrame(frame, text="Current Site", padding=5)
        detail.pack(fill=X, padx=10, pady=5)
        self.current_site_lbl = ttk.Label(detail, text="Waiting...", font=("Helvetica", 12))
        self.current_site_lbl.pack(anchor="w")

        results = ttk.LabelFrame(frame, text="Results", padding=5)
        results.pack(fill=BOTH, expand=True, padx=10, pady=5)
        cols = ("site", "products", "barcodes", "status")
        self.scrape_tree = ttk.Treeview(results, columns=cols, show="headings", height=12)
        for col, w, h in [("site",250,"Site"),("products",80,"Products"),("barcodes",80,"Barcodes"),("status",100,"Status")]:
            self.scrape_tree.heading(col, text=h)
            self.scrape_tree.column(col, width=w)
        self.scrape_tree.pack(fill=BOTH, expand=True)

    # ── Canonical ───────────────────────────────────────────────────
    def build_canonical_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="🔗 Canonical")
        ctrl = ttk.LabelFrame(frame, text="Canonicalisation", padding=10)
        ctrl.pack(fill=X, padx=10, pady=5)
        self.canon_btn = ttk.Button(ctrl, text="▶ Run Canonicalisation", command=self.start_canonical)
        self.canon_btn.pack(side=LEFT, padx=5)
        self.canon_stop_btn = ttk.Button(ctrl, text="⏹ Stop", command=self.stop_worker, state=DISABLED)
        self.canon_stop_btn.pack(side=LEFT, padx=5)
        ttk.Label(ctrl, text="Fuzzy threshold:").pack(side=LEFT, padx=10)
        self.confidence_var = IntVar(value=85)
        ttk.Spinbox(ctrl, from_=50, to=100, textvariable=self.confidence_var, width=5).pack(side=LEFT)

        preview = ttk.LabelFrame(frame, text="Canonical Preview", padding=5)
        preview.pack(fill=BOTH, expand=True, padx=10, pady=5)
        cols = ("id","name","brand","barcode","size","category","sources","min","max","avg")
        self.canon_tree = ttk.Treeview(preview, columns=cols, show="headings", height=15)
        for col, w, h in [("id",50,"ID"),("name",220,"Name"),("brand",120,"Brand"),("barcode",120,"Barcode"),
                          ("size",80,"Size"),("category",120,"Category"),("sources",60,"Srcs"),
                          ("min",60,"Min"),("max",60,"Max"),("avg",60,"Avg")]:
            self.canon_tree.heading(col, text=h)
            self.canon_tree.column(col, width=w)
        self.canon_tree.pack(fill=BOTH, expand=True)

    # ── Enrich ──────────────────────────────────────────────────────
    def build_enrich_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="✨ Enrich")
        fos_frame = ttk.LabelFrame(frame, text="FOS Stock Report", padding=10)
        fos_frame.pack(fill=X, padx=10, pady=5)
        self.fos_path_var = StringVar(value=self.config.get("fos_path", ""))
        ttk.Entry(fos_frame, textvariable=self.fos_path_var, width=60).pack(side=LEFT, padx=5)
        ttk.Button(fos_frame, text="Browse", command=self.browse_fos).pack(side=LEFT, padx=5)

        ctrl = ttk.Frame(frame)
        ctrl.pack(fill=X, padx=10, pady=5)
        ttk.Button(ctrl, text="▶ Enrich from FOS", command=self.start_enrich).pack(side=LEFT, padx=5)
        ttk.Button(ctrl, text="🔄 Cross-Domain Merge", command=self.start_crossdomain).pack(side=LEFT, padx=5)

        stats = ttk.LabelFrame(frame, text="Enrichment Stats", padding=10)
        stats.pack(fill=X, padx=10, pady=5)
        self.enrich_stats_lbl = ttk.Label(stats, text="No enrichment run yet.")
        self.enrich_stats_lbl.pack(anchor="w")

        preview = ttk.LabelFrame(frame, text="Matched Preview", padding=5)
        preview.pack(fill=BOTH, expand=True, padx=10, pady=5)
        cols = ("fos_name","canonical_name","fos_apn","barcode","confidence","type")
        self.enrich_tree = ttk.Treeview(preview, columns=cols, show="headings", height=12)
        for col, w, h in [("fos_name",250,"FOS Product"),("canonical_name",250,"Canonical Match"),
                          ("fos_apn",120,"FOS APN"),("barcode",120,"Barcode"),
                          ("confidence",80,"Conf"),("type",100,"Match Type")]:
            self.enrich_tree.heading(col, text=h)
            self.enrich_tree.column(col, width=w)
        self.enrich_tree.pack(fill=BOTH, expand=True)

    # ── Price ───────────────────────────────────────────────────────
    def build_price_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="📈 Price Analysis")
        ctrl = ttk.LabelFrame(frame, text="Price Analysis Controls", padding=10)
        ctrl.pack(fill=X, padx=10, pady=5)
        self.price_btn = ttk.Button(ctrl, text="▶ Run Price Analysis", command=self.start_price)
        self.price_btn.pack(side=LEFT, padx=5)
        self.price_stop_btn = ttk.Button(ctrl, text="⏹ Stop", command=self.stop_worker, state=DISABLED)
        self.price_stop_btn.pack(side=LEFT, padx=5)
        ttk.Button(ctrl, text="📂 Open Last Export", command=self.open_last_price).pack(side=LEFT, padx=5)

        stats = ttk.LabelFrame(frame, text="Analysis Stats", padding=10)
        stats.pack(fill=X, padx=10, pady=5)
        self.price_stats_lbl = ttk.Label(stats, text="No analysis run yet.")
        self.price_stats_lbl.pack(anchor="w")

        preview = ttk.LabelFrame(frame, text="Top Opportunities / Risks", padding=5)
        preview.pack(fill=BOTH, expand=True, padx=10, pady=5)
        cols = ("name","fos","comp_avg","gap","position","flag")
        self.price_tree = ttk.Treeview(preview, columns=cols, show="headings", height=15)
        for col, w, h in [("name",300,"Product"),("fos",80,"FOS $"),("comp_avg",80,"Comp Avg"),
                          ("gap",80,"Gap"),("position",90,"Position"),("flag",120,"Flag")]:
            self.price_tree.heading(col, text=h)
            self.price_tree.column(col, width=w)
        self.price_tree.pack(fill=BOTH, expand=True)

    # ── Export ──────────────────────────────────────────────────────
    def build_export_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="💾 Export")
        fmt = ttk.LabelFrame(frame, text="Formats", padding=10)
        fmt.pack(fill=X, padx=10, pady=5)
        self.export_master_var = BooleanVar(value=True)
        self.export_price_var = BooleanVar(value=True)
        self.export_shopify_var = BooleanVar(value=True)
        self.export_ebay_var = BooleanVar(value=True)
        ttk.Checkbutton(fmt, text="Master Workbook (5 sheets)", variable=self.export_master_var).pack(anchor="w")
        ttk.Checkbutton(fmt, text="Price Analysis (7 sheets)", variable=self.export_price_var).pack(anchor="w")
        ttk.Checkbutton(fmt, text="Shopify CSV", variable=self.export_shopify_var).pack(anchor="w")
        ttk.Checkbutton(fmt, text="eBay CSV", variable=self.export_ebay_var).pack(anchor="w")

        out = ttk.LabelFrame(frame, text="Output Directory", padding=10)
        out.pack(fill=X, padx=10, pady=5)
        self.export_dir_var = StringVar(value=str(self.export_dir))
        ttk.Entry(out, textvariable=self.export_dir_var, width=60).pack(side=LEFT, padx=5)
        ttk.Button(out, text="Browse", command=self.browse_export_dir).pack(side=LEFT, padx=5)

        btn = ttk.Frame(frame)
        btn.pack(fill=X, padx=10, pady=10)
        ttk.Button(btn, text="📤 Export All Selected", command=self.start_export).pack(side=LEFT, padx=5)
        ttk.Button(btn, text="📂 Open Export Folder", command=self.open_export_dir).pack(side=LEFT, padx=5)

        preview = ttk.LabelFrame(frame, text="Export Preview", padding=5)
        preview.pack(fill=BOTH, expand=True, padx=10, pady=5)
        self.export_preview = ScrolledText(preview, wrap="word", height=15, state=DISABLED)
        self.export_preview.pack(fill=BOTH, expand=True)
        self.update_export_preview()

    # ── Settings ──────────────────────────────────────────────────
    def build_settings_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="⚙️ Settings")
        db = ttk.LabelFrame(frame, text="Database", padding=10)
        db.pack(fill=X, padx=10, pady=5)
        self.db_path_var = StringVar(value=str(self.db_path))
        ttk.Entry(db, textvariable=self.db_path_var, width=70).pack(fill=X, padx=5)
        ttk.Button(db, text="Browse", command=self.browse_db).pack(anchor="e", padx=5, pady=2)

        scrape = ttk.LabelFrame(frame, text="Scraping", padding=10)
        scrape.pack(fill=X, padx=10, pady=5)
        self.aggressive_barcode_var = BooleanVar(value=self.config.get("aggressive_barcode", True))
        ttk.Checkbutton(scrape, text="Aggressive barcode extraction (slower, more accurate)", variable=self.aggressive_barcode_var).pack(anchor="w")

        ttk.Button(frame, text="💾 Save Settings", command=self.save_settings).pack(anchor="e", padx=10, pady=10)

    # ── Logs ────────────────────────────────────────────────────────
    def build_logs_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="📝 Logs")
        filt = ttk.Frame(frame)
        filt.pack(fill=X, padx=5, pady=2)
        ttk.Label(filt, text="Filter:").pack(side=LEFT)
        self.log_filter = ttk.Combobox(filt, values=["ALL","DEBUG","INFO","WARNING","ERROR"], width=10, state="readonly")
        self.log_filter.set("ALL")
        self.log_filter.pack(side=LEFT, padx=5)
        ttk.Button(filt, text="Clear", command=self.clear_logs).pack(side=LEFT, padx=5)
        ttk.Button(filt, text="Save", command=self.save_logs).pack(side=LEFT, padx=5)

        self.log_text = ScrolledText(frame, wrap="word", state=DISABLED, height=30)
        self.log_text.pack(fill=BOTH, expand=True, padx=5, pady=5)
        for lvl, fg in [("DEBUG","#7f8c8d"),("INFO","#2c3e50"),("WARNING","#f39c12"),("ERROR","#c0392b")]:
            self.log_text.tag_config(lvl, foreground=fg)

    # ── Status bar ──────────────────────────────────────────────────
    def build_status_bar(self):
        self.status_bar = ttk.Frame(self.root)
        self.status_bar.pack(side=BOTTOM, fill=X)
        self.status_lbl = ttk.Label(self.status_bar, text=f"Ready | DB: {self.db_path}")
        self.status_lbl.pack(side=LEFT, padx=10)
        self.status_time = ttk.Label(self.status_bar, text=datetime.now().strftime("%H:%M:%S"))
        self.status_time.pack(side=RIGHT, padx=10)
        self.update_clock()

    def update_clock(self):
        self.status_time.config(text=datetime.now().strftime("%H:%M:%S"))
        self.root.after(1000, self.update_clock)

    # ════════════════════════════════════════════════════════════════
    #  ACTIONS / WORKERS
    # ════════════════════════════════════════════════════════════════

    def run_worker(self, target, on_done=None):
        self.stop_event.clear()
        self.worker_thread = threading.Thread(target=self._worker_wrapper, args=(target, on_done), daemon=True)
        self.worker_thread.start()
        self.scrape_btn.config(state=DISABLED)
        self.stop_btn.config(state=NORMAL)
        self.canon_btn.config(state=DISABLED)
        self.canon_stop_btn.config(state=NORMAL)
        self.price_btn.config(state=DISABLED)
        self.price_stop_btn.config(state=NORMAL)

    def _worker_wrapper(self, target, on_done):
        try:
            result = target()
            if on_done:
                self.root.after(0, lambda: on_done(result))
        except Exception as e:
            logger.error(f"Worker error: {e}", exc_info=True)
            self.msg_queue.put({"type": "error", "message": str(e)})
        finally:
            self.root.after(0, self._worker_done)

    def _worker_done(self):
        self.scrape_btn.config(state=NORMAL)
        self.stop_btn.config(state=DISABLED)
        self.canon_btn.config(state=NORMAL)
        self.canon_stop_btn.config(state=DISABLED)
        self.price_btn.config(state=NORMAL)
        self.price_stop_btn.config(state=DISABLED)
        self.refresh_stats()

    def stop_worker(self):
        self.stop_event.set()
        self.log_activity("Stop requested", "warning")

    # ── Scrape ──────────────────────────────────────────────────────
    def start_scrape(self):
        targets = [s["domain"] for s in self.sites if s.get("enabled", True)]
        if not targets:
            messagebox.showwarning("No Sites", "No sites enabled. Enable sites in the Sites tab.")
            return
        self.scrape_tree.delete(*self.scrape_tree.get_children())
        self.scrape_progress["maximum"] = len(targets)
        self.scrape_progress["value"] = 0
        batch = datetime.now().strftime("%Y%m%d_%H%M")
        self.run_worker(
            lambda: core.run_shopify_batch(targets, batch, self.msg_queue, self.stop_event),
            on_done=self._on_scrape_done,
        )
        self.log_activity(f"Scraping {len(targets)} sites...", "info")

    def _on_scrape_done(self, result):
        for site in result.get("sites", []):
            icon = "✅" if site["status"] == "success" else "❌"
            self.scrape_tree.insert("", END, values=(
                site["site"], site.get("products",0), site.get("variants",0), site["status"]))
        self.log_activity(f"Scrape complete: {result['total_products']} products, {result['total_variants']} variants", "success")
        messagebox.showinfo("Scrape Complete", f"Products: {result['total_products']:,}\nVariants: {result['total_variants']:,}")

    # ── Canonical ───────────────────────────────────────────────────
    def start_canonical(self):
        self.canon_tree.delete(*self.canon_tree.get_children())
        self.run_worker(
            lambda: core.canonicalise(self.msg_queue, self.stop_event),
            on_done=self._on_canonical_done,
        )
        self.log_activity("Canonicalisation started...", "info")

    def _on_canonical_done(self, result):
        self.log_activity(f"Canonicalisation: {result['inserted']} new, {result['linked']} linked, {result['fuzzy_linked']} fuzzy. Total: {result['total_canonical']}", "success")
        # Load preview
        conn = core.get_conn()
        c = conn.cursor()
        c.execute("SELECT id, canonical_name, canonical_brand, canonical_barcode, canonical_size, canonical_category FROM canonical_products ORDER BY id DESC LIMIT 100")
        for row in c.fetchall():
            self.canon_tree.insert("", END, values=row)
        conn.close()

    # ── Enrich ──────────────────────────────────────────────────────
    def start_enrich(self):
        path = self.fos_path_var.get()
        if not path or not Path(path).exists():
            messagebox.showwarning("No FOS File", "Select your FOS_Cleaned.xlsx file first.")
            return
        self.run_worker(
            lambda: core.enrich_fos(path, self.msg_queue, self.stop_event),
            on_done=self._on_enrich_done,
        )
        self.log_activity("FOS enrichment started...", "info")

    def _on_enrich_done(self, result):
        self.enrich_stats_lbl.config(text=f"Exact: {result.get('exact',0)} | Fuzzy: {result.get('fuzzy',0)} | Total enriched: {result.get('total_enriched',0)}")
        self.log_activity(f"FOS enrichment complete: {result.get('total_enriched',0)} products enriched", "success")
        # Load preview
        conn = core.get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT cp.fos_stock_name, cp.canonical_name, cp.fos_apn, cp.canonical_barcode,
                   cp.fos_match_confidence, cp.fos_match_type
            FROM canonical_products cp WHERE cp.fos_apn IS NOT NULL ORDER BY cp.id DESC LIMIT 100
        """)
        for row in c.fetchall():
            self.enrich_tree.insert("", END, values=row)
        conn.close()

    def start_crossdomain(self):
        self.run_worker(
            lambda: core.cross_domain_barcode_merge(self.msg_queue, self.stop_event),
            on_done=self._on_crossdomain_done,
        )
        self.log_activity("Cross-domain barcode merge started...", "info")

    def _on_crossdomain_done(self, result):
        self.enrich_stats_lbl.config(text=f"Merged: {result['merged']} | Total: {result['total_canonical']} | With barcode: {result['with_barcode']} ({result['barcode_pct']:.1f}%)")
        self.log_activity(f"Cross-domain merge: {result['merged']} merged. Barcode coverage: {result['barcode_pct']:.1f}%", "success")

    # ── Price ────────────────────────────────────────────────────────
    def start_price(self):
        self.price_tree.delete(*self.price_tree.get_children())
        self.run_worker(
            lambda: core.price_analysis(self.msg_queue, self.stop_event),
            on_done=self._on_price_done,
        )
        self.log_activity("Price analysis started...", "info")

    def _on_price_done(self, result):
        stats = result.get("stats", {})
        total = result.get("rows", 0)
        text = f"Analyzed: {total} | Underpriced: {result.get('underpriced',0)} | Overpriced: {result.get('overpriced',0)} | Size mismatches: {result.get('size_mismatch',0)}"
        self.price_stats_lbl.config(text=text)
        self.log_activity(f"Price analysis exported to {result.get('path','')}", "success")
        messagebox.showinfo("Price Analysis", f"Exported: {result.get('path','')}\n{text}")
        # Load preview of top underpriced
        try:
            df = pd.read_excel(result["path"], sheet_name="Underpriced_Opportunities")
            for _, r in df.head(20).iterrows():
                self.price_tree.insert("", END, values=(
                    r.get("canonical_name","")[:50], f"${r.get('fos_sell_price',0):.2f}",
                    f"${r.get('comp_avg',0):.2f}", f"{r.get('price_gap',0):+.2f}",
                    r.get("price_position",""), r.get("mismatch_flags","")[:30]))
        except Exception:
            pass

    def open_last_price(self):
        exports = sorted(core.EXPORT_DIR.glob("price_analysis_*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
        if exports:
            webbrowser.open(f"file://{exports[0].resolve()}")
        else:
            messagebox.showinfo("No Exports", "No price analysis exports found.")

    # ── Export ────────────────────────────────────────────────────────
    def start_export(self):
        results = []
        if self.export_master_var.get():
            r = core.export_workbook()
            results.append(f"Master: {r['path']}")
        if self.export_price_var.get():
            r = core.price_analysis()
            results.append(f"Price: {r['path']}")
        self.update_export_preview("\n".join(results))
        self.log_activity("Export complete", "success")
        messagebox.showinfo("Export Complete", "\n".join(results))

    # ════════════════════════════════════════════════════════════════
    #  UI HELPERS
    # ════════════════════════════════════════════════════════════════

    def log_activity(self, msg, tag="info"):
        self.activity_text.config(state=NORMAL)
        self.activity_text.insert(END, f"{datetime.now().strftime('%H:%M:%S')} | {msg}\n", tag)
        self.activity_text.see(END)
        self.activity_text.config(state=DISABLED)

    def populate_sites(self):
        for s in self.sites:
            self.site_tree.insert("", END, values=(
                s["rank"], "☑" if s.get("enabled", True) else "☐",
                s["name"], s["domain"], s["type"], s.get("status","idle"),
                s.get("products",0), s.get("barcodes",0), s.get("difficulty","")))

    def toggle_site(self, event):
        item = self.site_tree.selection()[0]
        idx = self.site_tree.index(item)
        self.sites[idx]["enabled"] = not self.sites[idx].get("enabled", True)
        vals = list(self.site_tree.item(item, "values"))
        vals[1] = "☑" if self.sites[idx]["enabled"] else "☐"
        self.site_tree.item(item, values=tuple(vals))

    def select_all_sites(self):
        for s in self.sites:
            s["enabled"] = True
        for item in self.site_tree.get_children():
            vals = list(self.site_tree.item(item, "values"))
            vals[1] = "☑"
            self.site_tree.item(item, values=tuple(vals))

    def deselect_all_sites(self):
        for s in self.sites:
            s["enabled"] = False
        for item in self.site_tree.get_children():
            vals = list(self.site_tree.item(item, "values"))
            vals[1] = "☐"
            self.site_tree.item(item, values=tuple(vals))

    def load_custom_sites(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("CSV", "*.csv"), ("All", "*.*")])
        if not path:
            return
        try:
            if path.endswith(".json"):
                with open(path) as f:
                    data = json.load(f)
                self.sites = data
            else:
                import csv
                with open(path) as f:
                    reader = csv.DictReader(f)
                    self.sites = [{"rank":i+1, "name":r.get("name",""), "domain":r.get("domain",""), "enabled":True, "type":r.get("type",""), "difficulty":r.get("difficulty","Low")} for i,r in enumerate(reader)]
            for s in self.sites:
                s["status"] = "idle"; s["products"] = 0; s["barcodes"] = 0
            self.site_tree.delete(*self.site_tree.get_children())
            self.populate_sites()
            self.log_activity(f"Loaded {len(self.sites)} sites from {path}", "success")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def refresh_stats(self):
        try:
            stats = core.get_dashboard_stats()
            self.stat_labels["Sites Enabled"].config(text=str(len([s for s in self.sites if s.get("enabled")])))
            self.stat_labels["Total Scraped"].config(text=f"{stats.get('total_scraped',0):,}")
            self.stat_labels["With Barcodes"].config(text=f"{stats.get('with_barcodes',0):,}")
            self.stat_labels["Canonical Products"].config(text=f"{stats.get('canonical',0):,}")
            self.stat_labels["FOS Matches"].config(text=f"{stats.get('fos_enriched',0):,}")
        except Exception as e:
            logger.warning(f"Stats refresh failed: {e}")

    def ensure_db(self):
        try:
            core.init_db()
            self.log_activity(f"Database ready: {core.DB_PATH}", "info")
        except Exception as e:
            self.log_activity(f"DB init error: {e}", "error")

    def check_db(self):
        try:
            stats = core.get_dashboard_stats()
            msg = f"""Database Stats:
Sites scraped: {stats['sites_scraped']}
Total source products: {stats['total_scraped']:,}
With barcodes: {stats['with_barcodes']:,}
Canonical products: {stats['canonical']:,}
FOS enriched: {stats['fos_enriched']:,}"""
            messagebox.showinfo("Database Status", msg)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def clear_db_dialog(self):
        if messagebox.askyesno("Confirm", "DELETE all scraped data? This cannot be undone."):
            try:
                conn = core.get_conn()
                c = conn.cursor()
                c.execute("DELETE FROM canonical_sources")
                c.execute("DELETE FROM source_products")
                c.execute("DELETE FROM canonical_products")
                conn.commit()
                conn.close()
                self.log_activity("Database cleared", "warning")
                self.refresh_stats()
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def browse_fos(self):
        p = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx"), ("All", "*.*")])
        if p:
            self.fos_path_var.set(p)

    def browse_db(self):
        p = filedialog.asksaveasfilename(defaultextension=".db", filetypes=[("SQLite", "*.db"), ("All", "*.*")])
        if p:
            self.db_path_var.set(p)

    def browse_export_dir(self):
        p = filedialog.askdirectory()
        if p:
            self.export_dir_var.set(p)

    def open_export_dir(self):
        p = self.export_dir_var.get()
        if os.path.exists(p):
            webbrowser.open(f"file://{Path(p).resolve()}")
        else:
            messagebox.showwarning("Not Found", f"Directory not found: {p}")

    def update_export_preview(self, text=None):
        self.export_preview.config(state=NORMAL)
        self.export_preview.delete(1.0, END)
        if text is None:
            text = f"""Export preview (last run):
Master workbook: Canonical_Products, Source_Products, Price_Comparison, Shopify_Ready, eBay_Ready
Price analysis: All_Products, Underpriced, Overpriced, Size_Mismatch, At_Average, No_Price, Category_Summary
Output: {self.export_dir_var.get()}"""
        self.export_preview.insert(END, text)
        self.export_preview.config(state=DISABLED)

    def save_settings(self):
        self.config["db_path"] = self.db_path_var.get()
        self.config["export_dir"] = self.export_dir_var.get()
        self.config["aggressive_barcode"] = self.aggressive_barcode_var.get()
        core.save_config(self.config)
        self.log_activity("Settings saved", "info")
        messagebox.showinfo("Saved", "Settings saved to config.json")

    def load_config_dialog(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if p:
            with open(p) as f:
                self.config = json.load(f)
            self.db_path_var.set(self.config.get("db_path", ""))
            self.export_dir_var.set(self.config.get("export_dir", ""))
            self.fos_path_var.set(self.config.get("fos_path", ""))
            self.log_activity(f"Config loaded: {p}", "info")

    def save_config(self):
        core.save_config(self.config)
        self.log_activity("Config saved", "info")

    def show_about(self):
        messagebox.showinfo("About", "Pharma Scraper Pro v2.0\n\nAll-in-one canonical product database builder.\nScrape → Canonicalise → Enrich → Analyze → Export")

    def clear_logs(self):
        self.log_text.config(state=NORMAL)
        self.log_text.delete(1.0, END)
        self.log_text.config(state=DISABLED)

    def save_logs(self):
        p = filedialog.asksaveasfilename(defaultextension=".log")
        if p:
            with open(p, "w") as f:
                f.write(self.log_text.get(1.0, END))

    # ── Queue polling ────────────────────────────────────────────────
    def poll_queues(self):
        # Process messages
        while not self.msg_queue.empty():
            try:
                msg = self.msg_queue.get_nowait()
                if msg.get("type") == "site_start":
                    self.scrape_progress["value"] = msg.get("idx",0)
                    self.current_site_lbl.config(text=f"[{msg.get('idx',0)}/{msg.get('total',0)}] {msg.get('domain','')}")
                elif msg.get("type") == "site_progress":
                    self.current_site_lbl.config(text=f"{msg.get('domain','')}: {msg.get('products',0)} products, {msg.get('variants',0)} variants")
                elif msg.get("type") == "canonical_progress":
                    self.current_site_lbl.config(text=f"Canonicalising: {msg.get('inserted',0)} new, {msg.get('linked',0)} linked")
                elif msg.get("type") == "enrich_progress":
                    self.current_site_lbl.config(text=f"Fuzzy matches: {msg.get('fuzzy',0)}")
                elif msg.get("type") == "crossdomain_progress":
                    self.current_site_lbl.config(text=f"Merged: {msg.get('merged',0)}")
                elif msg.get("type") == "error":
                    self.log_activity(f"Error: {msg.get('message','')}", "error")
            except queue.Empty:
                break

        # Process logs
        while not self.log_queue.empty():
            try:
                record = self.log_queue.get_nowait()
                self.log_text.config(state=NORMAL)
                self.log_text.insert(END, record["message"] + "\n", record.get("level", "INFO"))
                self.log_text.see(END)
                self.log_text.config(state=DISABLED)
            except queue.Empty:
                break

        self.root.after(200, self.poll_queues)


# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    root = Tk()
    app = PharmaScraperProGUI(root)
    root.mainloop()
