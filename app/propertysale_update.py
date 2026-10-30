import requests
import pandas as pd
from tqdm import tqdm

SERVICE_URL = (
    "https://portal.spatial.nsw.gov.au/server/rest/services/"
    "Hosted/NSW_Address_Point_Formatted/FeatureServer/0/query"
)

BATCH_SIZE = 100

params = {
    "where": "1=1",
    "outFields": "*",
    "returnGeometry": "false",
    "f": "json",
}

# -------------------------------------------------
# Lấy toàn bộ OBJECTID trước
# -------------------------------------------------
print("Getting object ids...")

r = requests.get(
    SERVICE_URL,
    params={
        "where": "1=1",
        "returnIdsOnly": "true",
        "f": "json",
    },
)
r.raise_for_status()

object_ids = sorted(r.json()["objectIds"])
print(f"Total records: {len(object_ids):,}")

# -------------------------------------------------
# Download theo batch
# -------------------------------------------------
rows = []

for i in tqdm(range(0, len(object_ids), BATCH_SIZE)):
    ids = object_ids[i:i + BATCH_SIZE]

    r = requests.get(
        SERVICE_URL,
        params={
            "objectIds": ",".join(map(str, ids)),
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
        },
    )
    r.raise_for_status()

    data = r.json()

    for feature in data["features"]:
        rows.append(feature["attributes"])

df = pd.DataFrame(rows)

print(df.shape)
print(df.head())

df.to_csv("nsw_addresses.csv", index=False)
print("Saved to nsw_addresses.csv")