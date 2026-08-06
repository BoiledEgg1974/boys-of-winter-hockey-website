from pathlib import Path
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

targets = {
    "8bb1d661": [19, 22, 24],
    "ef8f14c3": [32, 41, 43],
    "8aba5351": [16, 17, 26, 34, 39, 40],
    "c75a0074": [723, 724, 729, 732, 734, 1860, 1861, 1870, 1874, 2991, 2997, 3006, 3008],
    "972cd330": [357, 358, 359, 364, 368, 383, 386],
    "7ad86c83": [293, 381, 383, 384, 396, 408, 410, 413],
}

base = Path(r"C:\Users\keeno\.cursor\projects\c-Users-keeno-Projects-Boys-Of-Winter-League\agent-transcripts")

for key, lines in targets.items():
    matches = list(base.rglob(f"{key}*.jsonl"))
    # prefer top-level transcript not subagents
    matches = [m for m in matches if "subagents" not in str(m)]
    if not matches:
        print("missing", key)
        continue
    p = matches[0]
    print("\n========", key, "========")
    all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    for i in lines:
        if i >= len(all_lines):
            continue
        try:
            obj = json.loads(all_lines[i])
        except Exception:
            continue
        role = obj.get("role") or obj.get("type")
        msg = obj.get("message") or obj
        content = msg.get("content") if isinstance(msg, dict) else None
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") in ("text", "output_text"):
                    parts.append(c.get("text") or "")
            text = "\n".join(parts)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 1200:
            text = text[:1200] + "..."
        print(f"\n-- line {i} [{role}] --\n{text}")
