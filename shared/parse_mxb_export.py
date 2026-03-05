"""Parse MX Bikes export HTML (from Documents\PiBoSo\MX Bikes\exports).

MX Bikes exports are not consistent across all servers/packs,
so this parser tries multiple patterns and returns a safe, simple structure.
"""

from bs4 import BeautifulSoup
import re
from typing import List, Dict

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def parse_export_html(html: str) -> Dict:
    soup = BeautifulSoup(html, "lxml")

    # Try to find a table that contains positions + names
    tables = soup.find_all("table")
    best_rows = []

    def score_table(tbl):
        txt = tbl.get_text(" ", strip=True).lower()
        score = 0
        for k in ["pos", "position", "name", "rider", "player"]:
            if k in txt:
                score += 1
        return score

    tables = sorted(tables, key=score_table, reverse=True)
    for tbl in tables[:5]:
        rows = []
        for tr in tbl.find_all("tr"):
            cols = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td","th"])]

            if not cols or len(cols) < 2:
                continue

            # find first integer in row as position
            pos = None
            for c in cols[:3]:
                m = re.match(r"^(\d{1,2})\b", c)
                if m:
                    pos = int(m.group(1))
                    break
            if not pos:
                continue

            # best guess for name column
            # often: [pos, name, time] or [pos, rider, bike, time]
            name = None
            for c in cols[1:]:
                if c and not re.match(r"^[0-9:.,+-]+$", c):
                    name = c
                    break
            if not name:
                continue

            time_text = None
            for c in cols:
                if re.search(r"\d+:\d+", c):
                    time_text = c
                    break

            rows.append({"position": pos, "name": name, "time": time_text})
        if len(rows) >= 3:
            best_rows = rows
            break

    # fallback: search for "1. Name" patterns
    if not best_rows:
        text = soup.get_text("\n")
        rows = []
        for line in text.splitlines():
            line = _clean(line)
            m = re.match(r"^(\d{1,2})\s*[.)-]\s*(.+)$", line)
            if m:
                pos = int(m.group(1))
                name = _clean(m.group(2))
                if len(name) > 1:
                    rows.append({"position": pos, "name": name, "time": None})
        if len(rows) >= 3:
            best_rows = rows

    best_rows = sorted(best_rows, key=lambda r: r["position"])
    return {"rows": best_rows}
