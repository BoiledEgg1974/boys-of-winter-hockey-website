from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from charset_normalizer import from_path


def detect_delimiter(sample_text: str) -> str:
    first_line = sample_text.split("\n")[0] if sample_text else ""
    return ";" if first_line.count(";") > first_line.count(",") else ","


def read_csv_normalized(path: Path, sep: str | None = None) -> pd.DataFrame:
    """Detect encoding, read CSV (FHM often uses ';'), normalize headers."""
    raw = path.read_bytes()
    result = from_path(str(path)).best()
    encoding = result.encoding if result else "utf-8"
    try:
        sample = raw.decode(encoding)
    except Exception:
        sample = raw.decode("utf-8", errors="replace")
    delimiter = sep or detect_delimiter(sample)
    df = pd.read_csv(path, encoding=encoding, sep=delimiter, dtype=str, keep_default_na=False)
    df.columns = [normalize_header(c) for c in df.columns]
    return df


def normalize_header(name: str) -> str:
    s = str(name).strip().lower().replace("\ufeff", "")
    s = s.replace("%", "_pct")
    for ch in (" ", "-", ".", "/"):
        s = s.replace(ch, "_")
    while "__" in s:
        s = s.replace("__", "_")
    # Note: FHM "+/-" becomes "+_" (slashes/minuses become single underscores after collapse).
    return s


def cell_val(row: dict, *keys: str):
    for k in keys:
        if k in row and row[k] is not None and str(row[k]).strip() != "":
            return str(row[k]).strip()
    return None


def to_int(val, default=None):
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def to_float(val, default=None):
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def to_bool(val, default=False):
    if val is None or val == "":
        return default
    v = str(val).strip().lower()
    return v in ("1", "true", "yes", "y", "t")


def parse_fhm_date(raw) -> date | None:
    """Parse FHM schedule/export dates. Exports often omit zero-padding (e.g. 1967-9-6, 1968-1-1), which breaks :meth:`date.fromisoformat`."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    token = s[:10] if len(s) >= 10 else s.split()[0]
    try:
        return date.fromisoformat(token)
    except ValueError:
        pass
    token = token.replace("/", "-")
    parts = token.split("-")
    if len(parts) >= 3:
        try:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            return date(y, m, d)
        except (ValueError, TypeError):
            return None
    return None


_CP1250_MOJIBAKE_HINTS = ("ĺ", "Ĺ", "ľ", "Ľ", "ř", "Ř", "č", "Č", "ď", "Ď", "ť", "Ť")


def repair_likely_cp1250_mojibake(text: str | None) -> str | None:
    """Repair common cp1250-vs-cp1252 mojibake in legacy FHM text fields.

    Example fixes:
    - ``Pĺhlsson`` -> ``Påhlsson``
    - ``Bjřrn`` -> ``Bjørn``
    """
    if text is None:
        return None
    s = str(text)
    if not s or not any(ch in s for ch in _CP1250_MOJIBAKE_HINTS):
        return s
    try:
        candidate = s.encode("cp1250").decode("cp1252")
    except UnicodeError:
        return s
    if not candidate:
        return s
    before = sum(s.count(ch) for ch in _CP1250_MOJIBAKE_HINTS)
    after = sum(candidate.count(ch) for ch in _CP1250_MOJIBAKE_HINTS)
    return candidate if after < before else s
