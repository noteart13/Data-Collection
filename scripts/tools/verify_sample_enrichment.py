"""
VERIFICATION & AUDIT SCRIPT FOR PROPERTYMASTER DATASET (100 SAMPLE PROPERTIES)
=============================================================================
Re-enriches nsw_sample_100_properties.json using the refactored enrichment logic,
verifies that all 6 audit issues are resolved, and outputs the fixed dataset.
"""

import json
import os
import re
import sys
from datetime import datetime

# UTF-8 stdout on Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

# Import refactored engine from bulk_enrich_master
from bulk_enrich_master import EnrichmentEngine, process_batch

class MockPoint:
    def __init__(self, id_val, payload):
        self.id = id_val
        self.payload = payload

def run_verification(input_file="nsw_sample_100_properties.json", output_file="nsw_sample_100_properties_fixed.json"):
    print("=" * 80)
    print("AUDIT & VERIFICATION REPORT: NSW 100 SAMPLE PROPERTIES RE-ENRICHMENT")
    print("=" * 80)

    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found!")
        return

    with open(input_file, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    total_records = len(raw_data)
    print(f"Loaded {total_records} properties from {input_file}.\n")

    # -------------------------------------------------------------
    # BASELINE METRICS (BEFORE)
    # -------------------------------------------------------------
    before_mismatches_cat = 0
    before_stale_sales = 0
    before_yield_mismatches = 0
    before_yield_values = set()
    before_fake_history_count = 0
    before_provenance_count = 0

    for d in raw_data:
        pl = d.get("payload", {})
        desc = pl.get("description", "").lower()
        ptype = pl.get("propertyType", "")
        cat = d.get("classifiedCategory", "")
        # Issue 1
        if re.search(r"\b(apartment|unit)\b", desc) and ptype == "House":
            before_mismatches_cat += 1

        # Issue 2 & 3
        off_sales = pl.get("officialSaleHistory") or []
        exp_price = pl.get("expectedPrice")
        if off_sales and exp_price:
            for s in off_sales:
                sp = float(s.get("price") or 0)
                sd = str(s.get("contractDate") or "")
                s_year = int(sd[:4]) if len(sd) >= 4 and sd[:4].isdigit() else 0
                if abs(sp - float(exp_price)) < 1.0 and s_year < 2025:
                    before_stale_sales += 1
                    break

        # Issue 4
        pricing = pl.get("pricing", {})
        rent = pricing.get("weeklyRent")
        yield_str = pricing.get("estimatedYield", "")
        if yield_str:
            before_yield_values.add(yield_str)
        if rent and exp_price and yield_str:
            actual_y = round((rent * 52.0 / exp_price) * 100.0, 1)
            stated_y = float(yield_str.replace("%", "").strip())
            if abs(actual_y - stated_y) > 0.2:
                before_yield_mismatches += 1

        # Issue 5
        for h in pl.get("historysale", []):
            if "Historical Land Titles" in h.get("agency", ""):
                before_fake_history_count += 1

        # Issue 6
        if pl.get("provenance"):
            before_provenance_count += 1

    # -------------------------------------------------------------
    # RUN RE-ENRICHMENT WITH REFACTORED ENGINE
    # -------------------------------------------------------------
    engine = EnrichmentEngine(seed=42)
    mock_points = [MockPoint(d["id"], dict(d.get("payload", {}))) for d in raw_data]

    updated_points, count = process_batch(mock_points, engine, force_update=True)

    fixed_data = []
    for pt in updated_points:
        cat_disp = pt.payload.get("propertyType", "House")
        fixed_data.append({
            "id": pt.id,
            "classifiedCategory": cat_disp,
            "payload": pt.payload
        })

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(fixed_data, f, indent=2, ensure_ascii=False)

    print(f"Successfully processed and saved {len(fixed_data)} re-enriched properties to {output_file}.\n")

    # -------------------------------------------------------------
    # POST-FIX METRICS (AFTER)
    # -------------------------------------------------------------
    after_mismatches_cat = 0
    after_stale_sales = 0
    after_yield_mismatches = 0
    after_yield_values = set()
    after_fake_history_count = 0
    after_provenance_count = 0
    official_sale_count = 0
    zoning_count = 0

    for d in fixed_data:
        pl = d.get("payload", {})
        desc = pl.get("description", "").lower()
        ptype = pl.get("propertyType", "")
        cat = d.get("classifiedCategory", "")

        # Issue 1 Check
        if re.search(r"\b(apartment|unit)\b", desc) and ptype == "House":
            after_mismatches_cat += 1

        # Issue 2 & 3 Check
        off_sales = pl.get("officialSaleHistory") or []
        exp_price = pl.get("expectedPrice")
        if off_sales and exp_price:
            official_sale_count += 1
            for s in off_sales:
                sp = float(s.get("price") or 0)
                sd = str(s.get("contractDate") or "")
                s_year = int(sd[:4]) if len(sd) >= 4 and sd[:4].isdigit() else 0
                if abs(sp - float(exp_price)) < 1.0 and s_year < 2025:
                    after_stale_sales += 1
                    break

        # Issue 4 Check
        pricing = pl.get("pricing", {})
        rent = pricing.get("weeklyRent")
        yield_str = pricing.get("estimatedYield", "")
        if yield_str:
            after_yield_values.add(yield_str)
        if rent and exp_price and yield_str:
            actual_y = round((rent * 52.0 / exp_price) * 100.0, 1)
            stated_y = float(yield_str.replace("%", "").strip())
            if abs(actual_y - stated_y) > 0.2:
                after_yield_mismatches += 1

        # Issue 5 Check
        for h in pl.get("historysale", []):
            if "Historical Land Titles" in h.get("agency", ""):
                after_fake_history_count += 1

        # Issue 6 Check
        if pl.get("provenance"):
            after_provenance_count += 1
        if pl.get("zoning"):
            zoning_count += 1

    # -------------------------------------------------------------
    # COMPARISON TABLE
    # -------------------------------------------------------------
    print("-" * 80)
    print(f"{'ISSUE / CRITERION':<42} | {'BEFORE':<15} | {'AFTER (FIXED)':<15}")
    print("-" * 80)
    print(f"{'1. Property Type Mismatches (Unit vs House)':<42} | {before_mismatches_cat:<15} | {after_mismatches_cat:<15}")
    print(f"{'2. Stale Historical expectedPrice (< 2025)':<42} | {before_stale_sales:<15} | {after_stale_sales:<15}")
    print(f"{'3. Inconsistent Yield Calculations':<42} | {before_yield_mismatches:<15} | {after_yield_mismatches:<15}")
    print(f"{'4. Unique Yield Rates Spectrum':<42} | {len(before_yield_values):<15} | {len(after_yield_values):<15} ({', '.join(sorted(list(after_yield_values))[:4])}...)")
    print(f"{'5. Fake History Sales Transactions':<42} | {before_fake_history_count:<15} | {after_fake_history_count:<15}")
    print(f"{'6. Structured Data Provenance Tagging':<42} | {before_provenance_count:<15} | {after_provenance_count:<15}")
    print("-" * 80)

    # Inspect First Property (181 Ballina Road)
    p1 = fixed_data[0]["payload"]
    print("\n--- SAMPLE #1 VERIFICATION (181 Ballina Road, Alstonville) ---")
    print(f"  Classification   : {p1.get('propertyType')} (Category: {fixed_data[0]['classifiedCategory']})")
    print(f"  Latest Sold Price: ${p1.get('lastSoldPrice'):,} on {p1.get('lastSoldDate')}")
    print(f"  2026 Exp. Price  : ${p1.get('expectedPrice'):,} AUD (Indexed Valuation)")
    print(f"  Weekly Rent      : ${p1.get('pricing', {}).get('weeklyRent'):,}/week")
    print(f"  Estimated Yield  : {p1.get('pricing', {}).get('estimatedYield')}")
    print(f"  Water / Council  : {p1.get('pricing', {}).get('waterBillPeriod')} | {p1.get('pricing', {}).get('councilBill')}")
    print(f"  Provenance Info  : {json.dumps(p1.get('provenance', {}).get('valuation', {}), indent=4)}")
    print("=" * 80)

if __name__ == "__main__":
    run_verification()
