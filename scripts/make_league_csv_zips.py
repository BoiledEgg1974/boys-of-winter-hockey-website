#!/usr/bin/env python3
"""
Zip each league's CSV folder to the Desktop as historicalcsv.zip, fantasycsv.zip, capcsv.zip.

First run: pick three folders. Paths are saved under %%LOCALAPPDATA%%\\BoysOfWinterLeague\\
Later runs: asks whether locations changed; if not, reuses saved paths.

  python scripts/make_league_csv_zips.py

Double-click: Make-League-Zips.bat in the repo root.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from zipfile import ZIP_DEFLATED, ZipFile

CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "BoysOfWinterLeague"
CONFIG_FILE = CONFIG_DIR / "csv_zip_paths.json"

KEYS = ("historical", "fantasy", "cap")
LABELS = {
    "historical": "Historical (BOWL-Historical) CSV folder",
    "fantasy": "Fantasy (BOWL-Fantasy) CSV folder",
    "cap": "Cap (BOWL-Cap) CSV folder",
}
ZIP_NAMES = {
    "historical": "historicalcsv.zip",
    "fantasy": "fantasycsv.zip",
    "cap": "capcsv.zip",
}


def desktop_dir() -> Path:
    """
    Folder Windows uses as your actual Desktop (avoids writing to a 'wrong' Desktop
    when OneDrive redirects the shell Desktop).
    """
    home = Path.home()
    if sys.platform == "win32":
        try:
            kw: dict = {
                "args": [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "[Environment]::GetFolderPath([Environment+SpecialFolder]::Desktop)",
                ],
                "capture_output": True,
                "text": True,
                "timeout": 20,
            }
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kw["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            cp = subprocess.run(**kw)
            if cp.returncode == 0:
                d = Path(cp.stdout.strip().strip("\r\n"))
                if d.is_dir():
                    return d
        except (OSError, subprocess.TimeoutExpired):
            pass
    for candidate in (home / "OneDrive" / "Desktop", home / "Desktop"):
        if candidate.is_dir():
            return candidate
    return home


def load_config() -> dict[str, str] | None:
    if not CONFIG_FILE.is_file():
        return None
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    folders = data.get("folders")
    if not isinstance(folders, dict):
        return None
    out: dict[str, str] = {}
    for k in KEYS:
        p = folders.get(k)
        if not isinstance(p, str) or not p.strip():
            return None
        out[k] = p
    return out


def resolve_saved(saved: dict[str, str]) -> dict[str, Path] | None:
    out: dict[str, Path] = {}
    for k in KEYS:
        try:
            p = Path(saved[k]).expanduser().resolve()
        except OSError:
            return None
        if not p.is_dir():
            return None
        out[k] = p
    return out


def save_config(folders: dict[str, Path]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "folders": {k: str(folders[k].resolve()) for k in KEYS},
    }
    CONFIG_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def validate_folders(folders: dict[str, Path]) -> str | None:
    for k in KEYS:
        p = folders[k]
        if not p.is_dir():
            return f"{LABELS[k]} is not a valid folder:\n{p}"
    return None


def zip_one_folder(src: Path, zip_path: Path) -> int:
    src = src.resolve()
    count = 0
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                arc = path.relative_to(src).as_posix()
                zf.write(path, arcname=arc)
                count += 1
    return count


def pick_folder(root: tk.Tk, title: str) -> Path | None:
    path = filedialog.askdirectory(title=title, parent=root, mustexist=True)
    return Path(path) if path else None


def pick_all_three(root: tk.Tk) -> dict[str, Path] | None:
    folders: dict[str, Path] = {}
    for k in KEYS:
        p = pick_folder(root, f"Select: {LABELS[k]}")
        if not p:
            return None
        folders[k] = p.resolve()
    err = validate_folders(folders)
    if err:
        messagebox.showerror("Invalid folder", err, parent=root)
        return None
    return folders


def initial_state(root: tk.Tk) -> dict[str, Path | None]:
    """Load saved paths or ask 'changed?' then return {key: Path|None}."""
    raw = load_config()
    if not raw:
        messagebox.showinfo(
            "First time",
            "Choose your three CSV folders.\n"
            "Use the Browse buttons, then Save locations, then Create zips.",
            parent=root,
        )
        return {k: None for k in KEYS}

    resolved = resolve_saved(raw)
    if not resolved:
        messagebox.showwarning(
            "Saved paths invalid",
            f"A saved folder no longer exists.\nPick all three again.\n({CONFIG_FILE})",
            parent=root,
        )
        return {k: None for k in KEYS}

    if messagebox.askyesno(
        "Folder locations",
        "Have any of the three CSV folder locations changed?",
        parent=root,
    ):
        picked = pick_all_three(root)
        if picked:
            return {k: picked[k] for k in KEYS}
        messagebox.showinfo(
            "Cancelled",
            "Keeping your previously saved folder locations.",
            parent=root,
        )

    return {k: resolved[k] for k in KEYS}


def run_app() -> None:
    root = tk.Tk()
    root.title("League CSV → Desktop zips")
    root.geometry("560x300")
    root.minsize(480, 260)

    state: dict[str, Path | None] = initial_state(root)

    main = ttk.Frame(root, padding=12)
    main.pack(fill=tk.BOTH, expand=True)

    ttk.Label(
        main,
        text="Zips go to: " + str(desktop_dir()),
        font=("Segoe UI", 9),
    ).pack(anchor=tk.W)

    path_vars = {k: tk.StringVar(value=str(state[k]) if state[k] else "(not set)") for k in KEYS}

    def refresh_paths() -> None:
        for k in KEYS:
            path_vars[k].set(str(state[k]) if state[k] else "(not set)")

    rows = ttk.Frame(main)
    rows.pack(fill=tk.BOTH, expand=True, pady=8)

    for i, k in enumerate(KEYS):
        ttk.Label(rows, text=LABELS[k] + ":").grid(row=i, column=0, sticky=tk.NW, pady=4)

        ttk.Entry(rows, textvariable=path_vars[k], width=52, state="readonly").grid(
            row=i, column=1, sticky=tk.EW, padx=6, pady=4
        )

        def make_browse(key: str) -> None:
            def _go() -> None:
                p = pick_folder(root, f"Select: {LABELS[key]}")
                if p:
                    state[key] = p.resolve()
                    refresh_paths()

            return _go

        ttk.Button(rows, text="Browse…", width=10, command=make_browse(k)).grid(
            row=i, column=2, pady=4
        )

    rows.columnconfigure(1, weight=1)

    btns = ttk.Frame(main)
    btns.pack(fill=tk.X, pady=(8, 0))

    def browse_all() -> None:
        picked = pick_all_three(root)
        if picked:
            for k in KEYS:
                state[k] = picked[k]
            refresh_paths()

    def save_paths() -> None:
        if any(state[k] is None for k in KEYS):
            messagebox.showwarning("Incomplete", "Choose all three folders first.", parent=root)
            return
        folders = {k: state[k] for k in KEYS}  # type: ignore[dict-item]
        err = validate_folders(folders)
        if err:
            messagebox.showerror("Invalid", err, parent=root)
            return
        save_config(folders)
        messagebox.showinfo("Saved", f"Saved to:\n{CONFIG_FILE}", parent=root)

    def make_zips() -> None:
        if any(state[k] is None for k in KEYS):
            messagebox.showwarning("Incomplete", "Choose all three folders first.", parent=root)
            return
        folders = {k: state[k] for k in KEYS}  # type: ignore[dict-item]
        err = validate_folders(folders)
        if err:
            messagebox.showerror("Invalid", err, parent=root)
            return
        desk = desktop_dir()
        lines = []
        for k in KEYS:
            name = ZIP_NAMES[k]
            zpath = desk / name
            n = zip_one_folder(folders[k], zpath)
            lines.append(f"{name}  ({n} files)\n{zpath}")
        messagebox.showinfo("Done", "\n\n".join(lines), parent=root)
        if sys.platform == "win32":
            try:
                os.startfile(desk)
            except OSError:
                pass

    ttk.Button(btns, text="Browse all 3…", command=browse_all).pack(side=tk.LEFT, padx=(0, 8))
    ttk.Button(btns, text="Save locations", command=save_paths).pack(side=tk.LEFT, padx=(0, 8))
    ttk.Button(btns, text="Create zips on Desktop", command=make_zips).pack(side=tk.LEFT)

    root.mainloop()


def main() -> int:
    try:
        run_app()
    except tk.TclError as e:
        print("Tkinter error (display / Tcl):", e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
