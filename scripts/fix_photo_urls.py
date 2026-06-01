"""Update 2025-26 player photo URLs to use the new FPL CDN path."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import pandas as pd

r = httpx.get("https://fantasy.premierleague.com/api/bootstrap-static/", timeout=30)
r.raise_for_status()
elements = r.json()["elements"]
print(f"Fetched {len(elements)} players from FPL API")

# key by element_id (p["id"]) since that's what players.csv stores
photo_map = {
    str(p["id"]): "https://resources.premierleague.com/premierleague25/photos/players/110x140/" + p["photo"].replace(".jpg", ".png")
    for p in elements
}

df = pd.read_csv("data/players.csv", dtype=str).fillna("")
mask = (df["season"] == "2025-26") & (df["type"] == "player")
updated = 0
for idx in df[mask].index:
    element_id = df.at[idx, "element_id"]
    if element_id in photo_map:
        df.at[idx, "photo_url"] = photo_map[element_id]
        updated += 1

df.to_csv("data/players.csv", index=False)
print(f"Updated {updated} photo URLs for 2025-26")
