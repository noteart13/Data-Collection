"""
Real-Time Property Listing Change Data Capture (CDC) Engine.
Detects status transitions (For Sale -> Sold), price changes/reductions,
and auction updates, generating atomic change events and maintaining audit history.
"""

import logging
import re
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass, field

logger = logging.getLogger("listing_cdc")


@dataclass
class CDCEvent:
    event_type: str  # STATUS_CHANGED, PRICE_REDUCED, PRICE_INCREASED, SOLD_RECORDED, NEW_LISTING
    point_id: str
    property_title: str
    timestamp: str
    details: Dict[str, Any] = field(default_factory=dict)


class ListingCDCEngine:
    """
    Compares freshly scraped listing snapshots against existing database state.
    Calculates deltas, detects lifecycle events, and enriches Qdrant payloads with audit history.
    """

    def __init__(self, now_iso: Optional[str] = None):
        self.now_iso = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def extract_numeric_price(price_str: Optional[str]) -> Optional[float]:
        """Extracts numeric dollar amount from freeform price strings (e.g. '$1,250,000 - Auction')."""
        if not price_str:
            return None
        # Look for $ followed by digits and commas
        match = re.search(r"\$\s*([0-9,]+)", price_str)
        if match:
            try:
                val = float(match.group(1).replace(",", ""))
                return val if val > 10000 else None
            except ValueError:
                pass
        return None

    def process_listing_update(
        self,
        point_id: str,
        existing_payload: Dict[str, Any],
        scraped_data: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], List[CDCEvent]]:
        """
        Processes updates for a property point:
        1. Compares old status vs new status
        2. Detects price changes and calculates percentage delta
        3. Appends audit events and historical records
        """
        payload = dict(existing_payload)
        events = []
        title = payload.get("title") or scraped_data.get("address") or f"Point {point_id}"

        old_status = payload.get("status", "unknown")
        new_status = scraped_data.get("status")

        # -------------------------------------------------------------
        # 1. STATUS TRANSITION DETECTION
        # -------------------------------------------------------------
        if new_status and new_status != old_status:
            payload["status"] = new_status
            payload["listingOption"] = new_status

            event = CDCEvent(
                event_type="STATUS_CHANGED",
                point_id=point_id,
                property_title=title,
                timestamp=self.now_iso,
                details={"old_status": old_status, "new_status": new_status}
            )
            events.append(event)
            logger.info(f"[CDC] Status change on {title}: {old_status} -> {new_status}")

            # If changed to 'sold', record in historysale
            if new_status == "sold":
                sold_price = scraped_data.get("sold_price") or self.extract_numeric_price(scraped_data.get("advertised_price"))
                sold_date = scraped_data.get("sold_date") or self.now_iso[:10]

                sold_item = {
                    "soldDate": sold_date,
                    "soldPrice": sold_price,
                    "agency": scraped_data.get("agency") or "Commercial Listing Scrape",
                    "datePrecision": "exact",
                    "isEstimatedDate": False
                }

                if "historysale" not in payload or not isinstance(payload["historysale"], list):
                    payload["historysale"] = []

                # Deduplicate history entry by date
                if not any(h.get("soldDate") == sold_date for h in payload["historysale"]):
                    payload["historysale"].insert(0, sold_item)

                payload["soldDateInfo"] = f"Sold for ${sold_price:,.0f} in {sold_date}" if sold_price else f"Sold in {sold_date}"

                sold_event = CDCEvent(
                    event_type="SOLD_RECORDED",
                    point_id=point_id,
                    property_title=title,
                    timestamp=self.now_iso,
                    details={"sold_date": sold_date, "sold_price": sold_price}
                )
                events.append(sold_event)

        # -------------------------------------------------------------
        # 2. PRICE CHANGE DETECTION
        # -------------------------------------------------------------
        advertised = scraped_data.get("advertised_price")
        if advertised:
            new_num_price = self.extract_numeric_price(advertised)
            old_num_price = payload.get("expectedPrice") or self.extract_numeric_price(payload.get("advertised_price"))

            if new_num_price and old_num_price and abs(new_num_price - old_num_price) >= 5000:
                delta = new_num_price - old_num_price
                pct_change = round((delta / old_num_price) * 100.0, 2)
                event_type = "PRICE_REDUCED" if delta < 0 else "PRICE_INCREASED"

                if "price_change_history" not in payload or not isinstance(payload["price_change_history"], list):
                    payload["price_change_history"] = []

                price_record = {
                    "date": self.now_iso,
                    "old_price": old_num_price,
                    "new_price": new_num_price,
                    "delta": delta,
                    "pct_change": pct_change,
                    "raw_text": advertised
                }
                payload["price_change_history"].append(price_record)
                payload["expectedPrice"] = new_num_price

                price_event = CDCEvent(
                    event_type=event_type,
                    point_id=point_id,
                    property_title=title,
                    timestamp=self.now_iso,
                    details=price_record
                )
                events.append(price_event)
                logger.info(f"[CDC] {event_type} on {title}: ${old_num_price:,.0f} -> ${new_num_price:,.0f} ({pct_change}%)")

        # -------------------------------------------------------------
        # 3. ATTRIBUTE ENRICHMENT
        # -------------------------------------------------------------
        # Update Bed, Bath, Car if provided by fresh scraper and previously missing
        features = scraped_data.get("features") or {}
        if features.get("bed") and not payload.get("bed"):
            payload["bed"] = features["bed"]
        if features.get("bath") and not payload.get("bath"):
            payload["bath"] = features["bath"]
        if features.get("garage") is not None:
            if "features" not in payload or not isinstance(payload["features"], dict):
                payload["features"] = {}
            if payload["features"].get("garage") in [0, None]:
                payload["features"]["garage"] = features["garage"]

        # Timestamp audit
        payload["updatedAt"] = self.now_iso
        if events:
            payload["last_cdc_event"] = events[-1].event_type

        return payload, events
