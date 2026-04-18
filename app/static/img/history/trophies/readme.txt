Trophy images for League History (Awards section)

- Primary folder: img/trophies/<league-slug>/ (e.g. img/trophies/bowl-fantasy/).
- Fallback: img/history/trophies/<league-slug>/ (e.g. generated SVG placeholders).
- Filename stem must match the slug of the award name: lowercase, non-alphanumeric
  replaced with underscores (same as award card titles after normalization).
  Example: HART TROPHY -> hart_trophy.png or hart_trophy.svg
- Supported extensions: .png, .webp, .jpg, .jpeg, .svg
- Replace generated SVG placeholders with your own art anytime; keep filenames.

Regenerate bowl-fantasy SVG placeholders:
  python scripts/generate_history_trophy_svgs.py
