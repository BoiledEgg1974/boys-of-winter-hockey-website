"""Build era-aware team logo/name resolvers (Historical / Cap / Fantasy) for templates and JSON APIs."""
from __future__ import annotations

import csv
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from flask import Flask, has_request_context, url_for

from app.config import league_slugs
from app.logo_urls import team_has_dedicated_league_logo, team_logo_url_for_team

if TYPE_CHECKING:
    from app.models import Team


@dataclass(frozen=True)
class SeasonTeamLogoBundle:
    season_team_logo_url: Callable[[object], str | None]
    team_logo_url_for_season_context: Callable[..., str]
    team_logo_url_present_franchise: Callable[..., str]
    season_team_name: Callable[[object], str | None]
    season_team_source_id: Callable[[object], str | None]
    draft_pick_team_logo_url: Callable[..., str | None]


_IMG_LOGO_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg", ".svg")

_PROCESS_LOGO_BUNDLES: dict[int, tuple[str, SeasonTeamLogoBundle]] = {}


def _logo_scan_dirs_for_league(app: Flask) -> list[Path]:
    if str(app.config.get("LEAGUE_SLUG") or "") not in league_slugs():
        return []
    team_logos_dir = Path(str(app.config.get("TEAM_LOGOS_DIR") or ""))
    static_root = Path(app.root_path) / "static"
    logo_scan_dirs: list[Path] = []
    if team_logos_dir.is_dir():
        logo_scan_dirs.append(team_logos_dir)
    if str(app.config.get("LEAGUE_SLUG") or "") == "bowl-cap":
        shared_hist = static_root / "logos" / "teams" / "bowl_historical"
        if shared_hist.is_dir() and shared_hist.resolve() != team_logos_dir.resolve():
            logo_scan_dirs.append(shared_hist)
    return logo_scan_dirs


def _bundle_input_fingerprint(app: Flask) -> str:
    """Stable key from CSV/logo inputs so we can reuse a built bundle across requests."""
    slug = str(app.config.get("LEAGUE_SLUG") or "")
    if slug not in league_slugs():
        return f"{slug}\x1einactive"
    parts: list[str] = [slug]
    parts.append(str(app.config.get("TEAM_LOGOS_REL_DIR") or "").replace("\\", "/").strip("/"))
    parts.append(str(Path(str(app.config.get("RAW_IMPORT_DIR") or ""))))
    raw_dir = Path(str(app.config.get("RAW_IMPORT_DIR") or ""))
    for fn in ("team_identity_history.csv", "team_season_records_template.csv"):
        p = raw_dir / fn
        if p.is_file():
            st = p.stat()
            parts.append(f"{fn}:{st.st_mtime_ns}:{st.st_size}")
        else:
            parts.append(f"{fn}:!")
    for scan_dir in _logo_scan_dirs_for_league(app):
        tag = scan_dir.as_posix()
        max_ns = 0
        tot_sz = 0
        n = 0
        try:
            for p in scan_dir.iterdir():
                if not p.is_file() or p.suffix.lower() not in _IMG_LOGO_SUFFIXES:
                    continue
                st = p.stat()
                if st.st_mtime_ns > max_ns:
                    max_ns = st.st_mtime_ns
                tot_sz += st.st_size
                n += 1
        except OSError:
            parts.append(f"dir:{tag}:err")
            continue
        parts.append(f"dir:{tag}:{n}:{max_ns}:{tot_sz}")
    return "\x1e".join(parts)


def build_season_team_logo_bundle(app: Flask) -> SeasonTeamLogoBundle:
    historical_team_logo_rel_by_id: dict[str, str] = {}
    historical_team_logo_rel_by_name: dict[str, str] = {}
    historical_team_name_by_id: dict[str, str] = {}
    historical_team_name_rows_by_id: dict[str, list[tuple[int, str]]] = {}
    historical_team_name_override_by_id_year: dict[tuple[str, int], str] = {}
    historical_team_logo_override_by_id_year: dict[tuple[str, int], str] = {}
    historical_team_logo_timeline_by_name_year: dict[tuple[str, int], str] = {}

    def _norm_team_logo_name(s: str) -> str:
        return " ".join(
            str(s or "")
            .lower()
            .replace(".", " ")
            .replace("-", " ")
            .replace("_", " ")
            .split()
        )

    static_root = Path(app.root_path) / (app.static_folder or "static")

    def _url_for_logo_rel(rel: str | None) -> str | None:
        if not rel:
            return None
        rel_s = str(rel).lstrip("/\\").replace("\\", "/")
        if rel_s.startswith("static/"):
            rel_s = rel_s[7:]
        path = static_root / rel_s
        if path.is_file():
            return url_for("static", filename=rel_s)
        parent = path.parent
        if parent.is_dir():
            want = path.name.lower()
            for candidate in parent.iterdir():
                if candidate.is_file() and candidate.name.lower() == want:
                    rel_hit = str(candidate.relative_to(static_root)).replace("\\", "/")
                    return url_for("static", filename=rel_hit)
        return None

    def _record_start_year(record: object) -> int | None:
        if isinstance(record, Mapping):
            for key in ("season_year", "start_year"):
                v = record.get(key)
                try:
                    if v is not None:
                        return int(v)
                except Exception:
                    pass
            label = record.get("season_year_label")
            if label and "-" in str(label):
                try:
                    return int(str(label).split("-", 1)[0])
                except Exception:
                    return None
            return None
        for attr in ("season_year", "start_year"):
            v = getattr(record, attr, None)
            try:
                if v is not None:
                    return int(v)
            except Exception:
                pass
        label = getattr(record, "season_year_label", None)
        if label and "-" in str(label):
            try:
                return int(str(label).split("-", 1)[0])
            except Exception:
                return None
        return None

    def _historical_name_for_tid(record: object, tid_s: str) -> str | None:
        sy = _record_start_year(record)
        if sy is not None:
            ovr = historical_team_name_override_by_id_year.get((tid_s, sy))
            if ovr:
                return ovr
        rows = historical_team_name_rows_by_id.get(tid_s) or []
        if sy is not None:
            for row_year, row_name in rows:
                if row_year == sy:
                    return row_name
        if tid_s in historical_team_name_by_id:
            return historical_team_name_by_id[tid_s]
        if rows:
            return rows[0][1]
        return None

    def _record_name_candidates(record: object) -> list[str]:
        out: list[str] = []
        for attr in ("team_name_override", "team_name"):
            v = getattr(record, attr, None)
            if v:
                out.append(_norm_team_logo_name(str(v)))
        if hasattr(record, "record"):
            rec = getattr(record, "record")
            if rec is not None:
                for attr in ("team_name_override", "team_name"):
                    v = getattr(rec, attr, None)
                    if v:
                        out.append(_norm_team_logo_name(str(v)))
        team_obj = getattr(record, "team", None)
        if team_obj is not None:
            for attr in ("full_display_name", "name", "city", "nickname"):
                v = getattr(team_obj, attr, None)
                if callable(v):
                    try:
                        v = v()
                    except Exception:
                        v = None
                if v:
                    out.append(_norm_team_logo_name(str(v)))
            city = getattr(team_obj, "city", None)
            nick = getattr(team_obj, "nickname", None)
            if city and nick:
                out.append(_norm_team_logo_name(f"{city} {nick}"))
        dedup: list[str] = []
        seen: set[str] = set()
        for nm in out:
            if nm and nm not in seen:
                seen.add(nm)
                dedup.append(nm)
        return dedup

    if str(app.config.get("LEAGUE_SLUG") or "") in league_slugs():
        team_logos_rel = str(app.config.get("TEAM_LOGOS_REL_DIR") or "logos/teams").replace("\\", "/").strip("/")
        team_logos_dir = Path(str(app.config.get("TEAM_LOGOS_DIR") or ""))
        static_root = Path(app.root_path) / "static"
        logo_scan_dirs = _logo_scan_dirs_for_league(app)
        for scan_dir in logo_scan_dirs:
            for p in scan_dir.iterdir():
                if not p.is_file() or p.suffix.lower() not in _IMG_LOGO_SUFFIXES:
                    continue
                try:
                    rel = p.relative_to(static_root)
                except ValueError:
                    continue
                rel_s = str(rel).replace("\\", "/")
                m = re.search(r"-t(\d+)$", p.stem.lower())
                if m:
                    tid = m.group(1)
                    historical_team_logo_rel_by_id[tid] = rel_s
                parts = p.stem.lower().split("-", 1)
                if len(parts) == 2 and parts[1].strip():
                    historical_team_logo_rel_by_name[_norm_team_logo_name(parts[1])] = rel_s
                tm = re.search(r"^(.+?)_(\d{4})-(present|\d{4})$", p.stem.lower())
                if tm:
                    key = _norm_team_logo_name(tm.group(1))
                    try:
                        yr0 = int(tm.group(2))
                    except Exception:
                        yr0 = -1
                    end_tok = tm.group(3)
                    if end_tok == "present":
                        yr1 = 2100
                    else:
                        try:
                            yr1 = int(end_tok)
                        except Exception:
                            yr1 = -1
                    if yr0 > 0 and yr1 > 0:
                        for yy in range(min(yr0, yr1), max(yr0, yr1) + 1):
                            historical_team_logo_timeline_by_name_year[(key, yy)] = rel_s
                sm = re.search(r"^(.+?)_(\d{4})$", p.stem.lower())
                if sm:
                    key = _norm_team_logo_name(sm.group(1))
                    try:
                        y1 = int(sm.group(2))
                    except Exception:
                        y1 = -1
                    if y1 > 0:
                        historical_team_logo_timeline_by_name_year[(key, y1)] = rel_s
        raw_dir = Path(str(app.config.get("RAW_IMPORT_DIR") or ""))
        tsr = raw_dir / "team_season_records_template.csv"
        if tsr.is_file():
            try:
                with tsr.open("r", encoding="utf-8-sig", newline="") as f:
                    sample = f.read(2048)
                    f.seek(0)
                    delim = ";" if sample.count(";") >= sample.count(",") else ","
                    rdr = csv.DictReader(f, delimiter=delim)
                    for row in rdr:
                        tid = (row.get("Team ID") or row.get("team_id") or "").strip()
                        nm = (row.get("Team Name Override") or row.get("team_name_override") or "").strip()
                        year = (row.get("Year") or row.get("season") or "").strip()
                        try:
                            start_year = int(str(year).split("-", 1)[0]) if year and "-" in year else int(year)
                        except Exception:
                            start_year = None
                        if tid and nm and tid not in historical_team_name_by_id:
                            historical_team_name_by_id[tid] = nm
                        if tid and nm and start_year is not None:
                            historical_team_name_rows_by_id.setdefault(tid, []).append((start_year, nm))
            except Exception:
                pass
        ident_csv = raw_dir / "team_identity_history.csv"
        if ident_csv.is_file():
            try:
                with ident_csv.open("r", encoding="utf-8-sig", newline="") as f:
                    sample = f.read(2048)
                    f.seek(0)
                    delim = ";" if sample.count(";") >= sample.count(",") else ","
                    rdr = csv.DictReader(f, delimiter=delim)
                    for row in rdr:
                        tid = str((row.get("team_fhm_id") or row.get("team_id") or "").strip())
                        name = str((row.get("team_name") or row.get("display_name") or "").strip())
                        logo = str((row.get("logo_file") or row.get("logo_file_override") or "").strip())
                        try:
                            y0 = int(
                                str(
                                    row.get("start_year")
                                    or row.get("year_start")
                                    or row.get("year")
                                    or ""
                                ).strip()
                            )
                        except Exception:
                            continue
                        try:
                            y1 = int(str(row.get("end_year") or row.get("year_end") or y0).strip())
                        except Exception:
                            y1 = y0
                        if logo and not logo.startswith("logos/"):
                            logo = f"{team_logos_rel}/{logo}"
                        for yy in range(min(y0, y1), max(y0, y1) + 1):
                            if tid:
                                if name:
                                    historical_team_name_override_by_id_year[(tid, yy)] = name
                                if logo:
                                    historical_team_logo_override_by_id_year[(tid, yy)] = logo
                            if name and logo:
                                historical_team_logo_timeline_by_name_year[
                                    (_norm_team_logo_name(name), yy)
                                ] = logo
            except Exception:
                pass
        if str(app.config.get("LEAGUE_SLUG") or "") in ("bowl-historical", "bowl-cap"):
            hist_logo_root = "logos/teams/bowl_historical"
            historical_team_logo_rel_by_name.setdefault(
                "ottawa senators", f"{hist_logo_root}/ott-ottawa-senators.png"
            )
            historical_team_logo_rel_by_name.setdefault(
                "montreal wanderers", f"{hist_logo_root}/mtw-montreal-wanderers.png"
            )
            historical_team_logo_rel_by_name.setdefault(
                "montreal maroons", f"{hist_logo_root}/montreal_maroons_1924.png"
            )
            historical_team_logo_rel_by_name.setdefault(
                "pittsburgh pirates", f"{hist_logo_root}/pit-t7.png"
            )
            historical_team_logo_rel_by_name.setdefault(
                "philadelphia quakers", f"{hist_logo_root}/philadelphia_quakers.png"
            )
            historical_team_logo_rel_by_name.setdefault(
                "st louis eagles", f"{hist_logo_root}/st__louis_eagles.png"
            )
            historical_team_logo_rel_by_name.setdefault(
                "quebec bulldogs", f"{hist_logo_root}/quebec_bulldogs.png"
            )
            historical_team_logo_rel_by_name.setdefault(
                "hamilton tigers", f"{hist_logo_root}/hamilton_tigers.png"
            )
            historical_team_logo_rel_by_name.setdefault(
                "new york americans", f"{hist_logo_root}/new_york_americans.png"
            )
            historical_team_name_override_by_id_year[("4", 1919)] = "Quebec Bulldogs"
            historical_team_logo_override_by_id_year[("4", 1919)] = f"{hist_logo_root}/quebec_bulldogs.png"
            historical_team_name_override_by_id_year[("4", 1920)] = "Hamilton Tigers"
            historical_team_logo_override_by_id_year[("4", 1920)] = f"{hist_logo_root}/hamilton_tigers.png"
            historical_team_name_override_by_id_year[("4", 1921)] = "Hamilton Tigers"
            historical_team_logo_override_by_id_year[("4", 1921)] = f"{hist_logo_root}/hamilton_tigers.png"
            historical_team_name_override_by_id_year[("4", 1922)] = "Hamilton Tigers"
            historical_team_logo_override_by_id_year[("4", 1922)] = f"{hist_logo_root}/hamilton_tigers.png"
            historical_team_name_override_by_id_year[("4", 1923)] = "Hamilton Tigers"
            historical_team_logo_override_by_id_year[("4", 1923)] = f"{hist_logo_root}/hamilton_tigers.png"
            historical_team_name_override_by_id_year[("4", 1924)] = "Hamilton Tigers"
            historical_team_logo_override_by_id_year[("4", 1924)] = f"{hist_logo_root}/hamilton_tigers.png"
            historical_team_name_override_by_id_year[("4", 1925)] = "New York Americans"
            historical_team_logo_override_by_id_year[("4", 1925)] = f"{hist_logo_root}/new_york_americans.png"
            historical_team_name_override_by_id_year[("4", 1926)] = "New York Americans"
            historical_team_logo_override_by_id_year[("4", 1926)] = f"{hist_logo_root}/new_york_americans.png"
            for yy in range(1967, 1970):
                historical_team_name_override_by_id_year[("13", yy)] = "Oakland Seals"
                historical_team_logo_override_by_id_year[("13", yy)] = f"{hist_logo_root}/oak-t120.png"
            for yy in range(1970, 1976):
                historical_team_name_override_by_id_year[("13", yy)] = "California Golden Seals"
                logo = (
                    f"{hist_logo_root}/california_golden_seals_1970-1973.png"
                    if yy <= 1973
                    else f"{hist_logo_root}/california_golden_seals_1974-1975.png"
                )
                historical_team_logo_override_by_id_year[("13", yy)] = logo
            for yy in range(1976, 1978):
                historical_team_name_override_by_id_year[("13", yy)] = "Cleveland Barons"
                historical_team_logo_override_by_id_year[("13", yy)] = (
                    f"{hist_logo_root}/cleveland_barons_1976-1977.png"
                )

    def season_team_logo_url(record: object) -> str | None:
        rec_map = record if isinstance(record, Mapping) else None
        if isinstance(record, Mapping):
            inner = record.get("record")
            if inner is not None:
                record = inner
                rec_map = inner if isinstance(inner, Mapping) else None

        logo_override_rel = getattr(record, "logo_file_override", None) or getattr(
            record, "team_logo_override_rel", None
        )
        if logo_override_rel is None and rec_map is not None:
            logo_override_rel = rec_map.get("logo_file_override") or rec_map.get("team_logo_override_rel")
        if logo_override_rel:
            hit = _url_for_logo_rel(str(logo_override_rel))
            if hit:
                return hit

        tid = getattr(record, "team_fhm_id_csv", None)
        if tid is None and rec_map is not None:
            tid = rec_map.get("team_fhm_id_csv")
        if tid is None and hasattr(record, "record"):
            tid = getattr(getattr(record, "record"), "team_fhm_id_csv", None)
        if tid is None:
            tid = getattr(record, "team_fhm_id", None)
        if tid is None and rec_map is not None:
            tid = rec_map.get("team_fhm_id")
        if tid is None:
            team_obj = getattr(record, "team", None)
            if team_obj is None and rec_map is not None:
                team_obj = rec_map.get("team")
            if team_obj is not None:
                tid = getattr(team_obj, "fhm_team_id", None)
        tid_s = str(tid or "").strip()
        sy = _record_start_year(record)

        def _timeline_logo_for_year(name_key: str, year: int) -> str | None:
            rel = historical_team_logo_timeline_by_name_year.get((name_key, year))
            if rel:
                return _url_for_logo_rel(rel)
            return None

        if tid_s and sy is not None:
            rel = historical_team_logo_override_by_id_year.get((tid_s, sy))
            if rel:
                hit = _url_for_logo_rel(rel)
                if hit:
                    return hit
        if sy is not None:
            for nm in _record_name_candidates(record):
                hit = _timeline_logo_for_year(nm, sy)
                if hit:
                    return hit
            name_from_tid = _historical_name_for_tid(record, tid_s) if tid_s else None
            if name_from_tid:
                hit = _timeline_logo_for_year(_norm_team_logo_name(name_from_tid), sy)
                if hit:
                    return hit
        # Non-era franchise files (-t{id}) are only for requests without a season year.
        if sy is None:
            if tid_s and tid_s in historical_team_logo_rel_by_id:
                hit = _url_for_logo_rel(historical_team_logo_rel_by_id[tid_s])
                if hit:
                    return hit
            name_from_tid = _historical_name_for_tid(record, tid_s) if tid_s else None
            if name_from_tid:
                nm = _norm_team_logo_name(name_from_tid)
                rel = historical_team_logo_rel_by_name.get(nm)
                if rel:
                    hit = _url_for_logo_rel(rel)
                    if hit:
                        return hit

            for nm in _record_name_candidates(record):
                rel = historical_team_logo_rel_by_name.get(nm)
                if rel:
                    hit = _url_for_logo_rel(rel)
                    if hit:
                        return hit

        team_obj = getattr(record, "team", None)
        if team_obj is None and rec_map is not None:
            team_obj = rec_map.get("team")
        if sy is not None:
            return None
        if team_obj:
            return team_logo_url_for_team(team_obj)
        return None

    def season_team_name(record: object) -> str | None:
        rec_map = record if isinstance(record, Mapping) else None
        if isinstance(record, Mapping):
            inner = record.get("record")
            if inner is not None:
                record = inner
                rec_map = inner if isinstance(inner, Mapping) else None
        ovr = getattr(record, "team_name_override", None)
        if ovr is None and rec_map is not None:
            ovr = rec_map.get("team_name_override")
        if ovr and str(ovr).strip():
            return str(ovr).strip()
        tid = getattr(record, "team_fhm_id_csv", None)
        if tid is None and rec_map is not None:
            tid = rec_map.get("team_fhm_id_csv")
        if tid is None:
            tid = getattr(record, "team_fhm_id", None)
        if tid is None and rec_map is not None:
            tid = rec_map.get("team_fhm_id")
        team_obj = getattr(record, "team", None)
        if team_obj is None and rec_map is not None:
            team_obj = rec_map.get("team")
        if tid is None and team_obj is not None:
            tid = getattr(team_obj, "fhm_team_id", None)
        tid_s = str(tid or "").strip()
        sy = _record_start_year(record)
        if tid_s and sy is not None:
            id_ovr = historical_team_name_override_by_id_year.get((tid_s, sy))
            if id_ovr:
                return id_ovr
        rows: list[tuple[int, str]] = []
        if tid_s:
            rows = historical_team_name_rows_by_id.get(tid_s) or []
            if sy is not None:
                for row_year, row_name in rows:
                    if row_year == sy:
                        return row_name
        if team_obj is not None:
            return team_obj.full_display_name()
        if tid_s:
            if tid_s in historical_team_name_by_id:
                return historical_team_name_by_id[tid_s]
            if rows:
                return rows[0][1]
        return None

    def season_team_source_id(record: object) -> str | None:
        rec_map = record if isinstance(record, Mapping) else None
        if isinstance(record, Mapping):
            inner = record.get("record")
            if inner is not None:
                record = inner
                rec_map = inner if isinstance(inner, Mapping) else None
        tid = getattr(record, "team_fhm_id_csv", None)
        if tid is None and rec_map is not None:
            tid = rec_map.get("team_fhm_id_csv")
        if tid is None:
            tid = getattr(record, "team_fhm_id", None)
        if tid is None and rec_map is not None:
            tid = rec_map.get("team_fhm_id")
        tid_s = str(tid or "").strip()
        return tid_s or None

    def team_logo_url_for_season_context(team: Any, season: object | int | None) -> str:
        if team is None:
            return url_for("static", filename="logos/teams/placeholder.svg")
        slug = str(app.config.get("LEAGUE_SLUG") or "")
        sy: int | None
        if isinstance(season, int):
            sy = int(season)
        elif season is not None:
            sy = getattr(season, "start_year", None)
            if sy is not None:
                sy = int(sy)
        else:
            sy = None
        if sy is not None and slug in ("bowl-historical", "bowl-cap", "bowl-fantasy"):
            tid = getattr(team, "fhm_team_id", None)
            tid_s = str(tid).strip() if tid is not None and str(tid).strip() else None
            proxy = SimpleNamespace(
                team=team,
                start_year=int(sy),
                season_year=int(sy),
                team_fhm_id_csv=tid_s,
            )
            era = season_team_logo_url(proxy)
            if era:
                return era
            if team_has_dedicated_league_logo(team):
                return team_logo_url_for_team(team)
            return url_for("static", filename="logos/teams/placeholder.svg")
        return team_logo_url_for_team(team)

    _ERA_LEAGUE_SLUGS = ("bowl-historical", "bowl-cap", "bowl-fantasy")

    def draft_pick_team_logo_url(
        pick: object | None,
        *,
        tm_fhm: int | None = None,
        season_year: int | None = None,
    ) -> str | None:
        """Logo for the team that drafted a pick, using draft year for era-correct art."""
        sy = season_year
        if sy is None and pick is not None:
            sy = getattr(pick, "draft_year", None)
        try:
            sy_int = int(sy) if sy is not None else None
        except (TypeError, ValueError):
            sy_int = None
        team = getattr(pick, "team", None) if pick is not None else None
        tid = tm_fhm
        if tid is None and team is not None:
            tid = getattr(team, "fhm_team_id", None)
        rec: dict[str, object | None] = {
            "team": team,
            "season_year": sy_int,
            "team_fhm_id": tid,
        }
        slug = str(app.config.get("LEAGUE_SLUG") or "")
        if slug in _ERA_LEAGUE_SLUGS and sy_int is not None:
            url = season_team_logo_url(rec)
            if url:
                return url
            if team is not None:
                return team_logo_url_for_season_context(team, sy_int)
            return url_for("static", filename="logos/teams/placeholder.svg")
        if team is not None:
            return team_logo_url_for_team(team)
        return season_team_logo_url(rec)

    # End-of-timeline year used when scanning `*_YYYY-present` filenames (see logo bundle scan).
    _FRANCHISE_LOGO_END_YEAR = 2100

    def team_logo_url_present_franchise(team: Any) -> str:
        if team is None:
            return url_for("static", filename="logos/teams/placeholder.svg")
        slug = str(app.config.get("LEAGUE_SLUG") or "")
        if slug not in ("bowl-historical", "bowl-cap", "bowl-fantasy"):
            return team_logo_url_for_team(team)
        # Prefer the league roster logo (e.g. bowl_cap/phx-t26.png) over franchise timeline rows that
        # reuse the same FHM id across relocated clubs (Winnipeg → Phoenix → Utah on id 26).
        if team_has_dedicated_league_logo(team):
            return team_logo_url_for_team(team)
        tid = getattr(team, "fhm_team_id", None)
        tid_s = str(tid).strip() if tid is not None and str(tid).strip() else None
        proxy = SimpleNamespace(
            team=team,
            start_year=_FRANCHISE_LOGO_END_YEAR,
            season_year=_FRANCHISE_LOGO_END_YEAR,
            team_fhm_id_csv=tid_s,
        )
        era = season_team_logo_url(proxy)
        if era:
            return era
        return team_logo_url_for_team(team)

    return SeasonTeamLogoBundle(
        season_team_logo_url=season_team_logo_url,
        team_logo_url_for_season_context=team_logo_url_for_season_context,
        team_logo_url_present_franchise=team_logo_url_present_franchise,
        season_team_name=season_team_name,
        season_team_source_id=season_team_source_id,
        draft_pick_team_logo_url=draft_pick_team_logo_url,
    )


def get_season_team_logo_bundle(app: Flask | None = None) -> SeasonTeamLogoBundle:
    """Return logo/name resolvers; cache per worker while inputs are unchanged.

    Rebuilds when raw CSVs or team logo files change (see ``_bundle_input_fingerprint``).
    Within a request, also assigns ``g._season_team_logo_bundle`` for consistency.
    """
    from flask import current_app, g

    app = app or current_app
    fp = _bundle_input_fingerprint(app)
    aid = id(app)
    ent = _PROCESS_LOGO_BUNDLES.get(aid)
    if ent is not None and ent[0] == fp:
        b = ent[1]
        if has_request_context():
            g._season_team_logo_bundle = b
        return b

    b = build_season_team_logo_bundle(app)
    _PROCESS_LOGO_BUNDLES[aid] = (fp, b)
    if has_request_context():
        g._season_team_logo_bundle = b
    return b


def dashboard_team_logo_url(team: Team | None, season_start_year: int | None) -> str:
    """Era-accurate team logo for homepage dashboard JSON and similar API payloads."""
    return get_season_team_logo_bundle().team_logo_url_for_season_context(team, season_start_year)
