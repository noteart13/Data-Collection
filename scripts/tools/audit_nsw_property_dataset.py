#!/usr/bin/env python3
"""Read-only audit for thehooklab/nsw-property sales Parquet."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq


FEATURE_COLUMNS = ("num_bed", "num_bath", "num_parking", "area", "land_area")
IDENTITY_COLUMNS = (
    "property_id",
    "house_number",
    "street_name",
    "locality",
    "postcode",
    "contract_date",
    "purchase_price",
    "property_type",
    "lat",
    "lon",
)


def normalise(value) -> str:
    return " ".join(str(value or "").upper().split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--house-number", default="602/8")
    parser.add_argument("--street", default="DUNTROON AVENUE")
    parser.add_argument("--locality", default="ST LEONARDS")
    parser.add_argument("--postcode", default="2065")
    args = parser.parse_args()

    parquet = pq.ParquetFile(args.path)
    names = set(parquet.schema_arrow.names)
    requested = [name for name in (*IDENTITY_COLUMNS, *FEATURE_COLUMNS) if name in names]
    print(f"rows={parquet.metadata.num_rows:,}")
    print(f"row_groups={parquet.metadata.num_row_groups}")
    print("columns=" + ",".join(parquet.schema_arrow.names))
    missing = sorted(set((*IDENTITY_COLUMNS, *FEATURE_COLUMNS)) - names)
    print("missing_expected_columns=" + (",".join(missing) if missing else "none"))

    table = parquet.read(columns=requested)
    if "source" in names and "source" not in table.column_names:
        table = table.append_column("source", parquet.read(columns=["source"])["source"])
    if "source" in table.column_names:
        source_values = pc.value_counts(table["source"]).to_pylist()
        sources = Counter({str(item["values"]): item["counts"] for item in source_values})
        print(f"source_values={sources.most_common(20)}")
    for name in FEATURE_COLUMNS:
        if name not in names:
            continue
        column = table[name]
        non_null = len(column) - column.null_count
        print(
            f"field={name} non_null={non_null:,} "
            f"coverage={non_null / max(len(column), 1):.4%}"
        )
        values = pc.value_counts(column).to_pylist()
        common = Counter({str(item["values"]): item["counts"] for item in values})
        print(f"field={name} top_values={common.most_common(10)}")

    target = {
        "house_number": normalise(args.house_number),
        "street_name": normalise(args.street),
        "locality": normalise(args.locality),
        "postcode": normalise(args.postcode).removesuffix(".0"),
    }
    matches = []
    street_matches = []
    feature_samples = []
    for batch in table.to_batches(max_chunksize=100_000):
        rows = batch.to_pylist()
        for row in rows:
            postcode = normalise(row.get("postcode")).removesuffix(".0")
            if (
                normalise(row.get("house_number")) == target["house_number"]
                and normalise(row.get("street_name")) == target["street_name"]
                and normalise(row.get("locality")) == target["locality"]
                and postcode == target["postcode"]
            ):
                matches.append(row)
            if (
                normalise(row.get("street_name")) == target["street_name"]
                and normalise(row.get("locality")) == target["locality"]
                and postcode == target["postcode"]
            ):
                street_matches.append(row)
            if row.get("num_bed") is not None and len(feature_samples) < 20:
                feature_samples.append(row)
    print(f"exact_target_matches={len(matches)}")
    for row in matches[:20]:
        print(f"target_row={row}")
    print(f"target_street_matches={len(street_matches)}")
    for row in street_matches[:20]:
        print(f"target_street_row={row}")
    for row in feature_samples:
        print(f"feature_sample={row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
