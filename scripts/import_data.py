"""Run CSV import pipeline. Usage: python scripts/import_data.py"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_pipeline.runner import run_import  # noqa: E402

if __name__ == "__main__":
    run_import()
