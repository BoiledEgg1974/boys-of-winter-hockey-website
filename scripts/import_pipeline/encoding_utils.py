from __future__ import annotations

import io
import re
from datetime import date
from pathlib import Path

import pandas as pd

# Unused in Windows-1252; FHM occasionally emits them (e.g. Jüri as J + 0x81 + ü).
_CP1252_UNUSED = frozenset({0x81, 0x8D, 0x8F, 0x90, 0x9D})


def detect_delimiter(sample_text: str) -> str:
    first_line = sample_text.split("\n")[0] if sample_text else ""
    return ";" if first_line.count(";") > first_line.count(",") else ","


def decode_fhm_csv_bytes(raw: bytes) -> tuple[str, str]:
    """Decode FHM export bytes. Prefer UTF-8, then Windows-1252.

    Auto-sniffers often label these files cp1250 or cp775, which turns ``Åke`` into
    ``Ĺke``, ``Trois-Rivières`` into ``Trois-Rivičres``, and ``Bründl`` into ``Br³ndl``.
    """
    if not raw:
        return "", "utf-8"
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16-le"), "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16-be"), "utf-16-be"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    cleaned = bytes(b for b in raw if b not in _CP1252_UNUSED)
    try:
        return cleaned.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def read_csv_normalized(path: Path, sep: str | None = None) -> pd.DataFrame:
    """Detect encoding, read CSV (FHM often uses ';'), normalize headers."""
    raw = path.read_bytes()
    sample, _encoding = decode_fhm_csv_bytes(raw)
    delimiter = sep or detect_delimiter(sample)
    df = pd.read_csv(io.StringIO(sample), sep=delimiter, dtype=str, keep_default_na=False)
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


def fhm_scoring_period_to_int(period_raw, default: int = 1) -> int:
    """Map FHM ``Period`` cells (``1``–``3``, ``OT1``, ``OT2``, …) to stored period integers.

    Regulation stays 1–3. ``OT1`` → 4, ``OT2`` → 5, so downstream boxscore code can treat
    ``period > 3`` as overtime. Plain ``OT`` is treated as the first OT frame (4).

    ``to_int("OT1", 1)`` incorrectly becomes ``1``; use this for boxscore goal/penalty rows.
    """
    if period_raw is None:
        return default
    s = str(period_raw).strip().upper().replace(",", ".")
    if not s:
        return default
    m_ot = re.match(r"^OT(\d+)$", s)
    if m_ot:
        return 3 + int(m_ot.group(1))
    if s == "OT":
        return 4
    try:
        v = int(float(s))
    except (TypeError, ValueError):
        return default
    return v if v >= 1 else default


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


def clean_fhm_text(text: str | None) -> str | None:
    """Repair cp1250-vs-cp1252 mojibake left in already-imported FHM text.

    Example fixes:
    - ``Pĺhlsson`` -> ``Påhlsson``
    - ``Bjřrn`` -> ``Bjørn``
    - ``Trois-Rivičres`` -> ``Trois-Rivières``
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


def repair_likely_cp1250_mojibake(text: str | None) -> str | None:
    """Backward-compatible alias for :func:`clean_fhm_text`."""
    return clean_fhm_text(text)
