import json
import html as html_lib
import os
import re
from datetime import datetime
from urllib.parse import urlparse

import json5
from bs4 import BeautifulSoup

from operations.storage import gcs


try:
    with open("suburb_city_mapping_v1.json", "r", encoding="utf-8") as f:
        data = json5.load(f)

    lookup = {
        (item.get("state", "").lower(), item.get("suburb", "").lower()): item.get("city")
        for item in data or []
    }

except Exception:
    lookup = {}


UNIT_TO_SQM = {
    "m2": 1,
    "sqm": 1,
    "sq m": 1,
    "m²": 1,
    "sqm.": 1,
    "sq. m": 1,
    "ha": 10000,
    "hectare": 10000,
    "km2": 1_000_000,
    "km²": 1_000_000,
    "acre": 4046.86,
    "ac": 4046.86,
    "ft2": 0.092903,
    "sqft": 0.092903,
    "ft²": 0.092903,
}


def safe_get(data, path, default=None):
    cur = data

    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and isinstance(key, int):
            if 0 <= key < len(cur):
                cur = cur[key]
            else:
                return default
        else:
            return default

        if cur is None:
            return default

    return cur


def safe_text(tag, default=None):
    return tag.get_text(strip=True) if tag else default


def extract_balanced_object(text, marker):
    pos = text.find(marker)
    if pos == -1:
        return None

    start = text.find("{", pos)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    quote = None

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue

        if char in ("'", '"'):
            in_string = True
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]

    return None


def extract_metadata(soup, html=""):
    """
    Extract property.com.au ArgonautExchange metadata safely.
    """
    source = html or ""

    if not source and soup is not None:
        script = soup.find("script", string=re.compile("ArgonautExchange"))
        source = script.string if script and script.string else ""

    if not source:
        return {}

    raw = extract_balanced_object(source, "window.ArgonautExchange")

    if not raw:
        return {}

    try:
        argonaut = json5.loads(raw)
    except Exception:
        return {}

    cache_raw = safe_get(
        argonaut,
        ["property-com-au_pca-property-mfe", "URQL_CACHE"]
    )

    if not cache_raw:
        return {}

    try:
        cache = json5.loads(cache_raw)
    except Exception:
        return {}

    best_page = {}

    for item in cache.values():
        data_raw = item.get("data") if isinstance(item, dict) else None

        if not data_raw:
            continue

        try:
            data = json5.loads(data_raw)
        except Exception:
            continue

        page = data.get("propertyPageBySlugV3")

        if not isinstance(page, dict):
            continue

        if len(page.keys()) > len(best_page.keys()):
            best_page = page

    return best_page


class PropertyMeta:
    def __init__(self, html, url, datatype):
        self.url = url
        self.html = html or ""
        self.datatype = datatype

        self.soup = BeautifulSoup(self.html, "html.parser")
        self.meta = extract_metadata(self.soup, self.html)

        # Writing the complete metadata for every property is expensive and is
        # unsafe when several mappings run concurrently. Enable this diagnostic
        # explicitly only when investigating one page.
        debug_meta_path = os.environ.get("PROPERTY_META_DEBUG_PATH")
        if debug_meta_path:
            try:
                with open(debug_meta_path, "w", encoding="utf-8") as file:
                    json5.dump(self.meta, file, indent=4, ensure_ascii=False)
            except Exception:
                pass

    def safe_get(self, data, path, default=None):
        return safe_get(data, path, default)

    def safe_text(self, tag, default=None):
        return safe_text(tag, default)

    def split_name(self, full_name):
        full_name = full_name or ""
        parts = full_name.strip().split()

        if len(parts) == 0:
            return "", ""

        if len(parts) == 1:
            return parts[0], ""

        return " ".join(parts[:-1]), parts[-1]

    def get_city(self, state_input, suburb_input, lookup_dict):
        state_input = state_input or ""
        suburb_input = suburb_input or ""
        return lookup_dict.get((state_input.lower(), suburb_input.lower()))

    def timeline_events(self):
        groups = self.safe_get(self.meta, ["timelineV4", "eventGroups"], [])
        events = []

        for group in groups or []:
            if not isinstance(group, dict):
                continue
            for event in group.get("events") or []:
                if isinstance(event, dict):
                    events.append(event)

        return events

    def current_event(self):
        events = self.timeline_events()
        status = self.safe_get(self.meta, ["header", "status", "badgeV2", "text"])

        if status:
            for event in events:
                if self.safe_get(event, ["badgeV3", "text"]) == status:
                    return event

        return events[0] if events else {}

    def image_url(self, image):
        if not isinstance(image, dict):
            return None

        return (
            image.get("desktopFullScreenImageJpgUri")
            or image.get("mobileFullScreenImageJpgUri")
            or image.get("jpgUri")
            or image.get("url")
        )

    async def images(self):
        images = []
        floorplan_start = None

        for item in self.safe_get(self.meta, ["mediaGalleryV2", "mediaCounts"], []) or []:
            if item.get("title") == "Floorplans":
                try:
                    floorplan_start = int(item.get("imageIndexFrom"))
                except Exception:
                    floorplan_start = None

        for index, item in enumerate(self.safe_get(self.meta, ["mediaGalleryV2", "items"], []) or []):
            image = item.get("image") if isinstance(item, dict) else None
            url = self.image_url(image)

            if not url:
                continue

            category = "floorplan" if floorplan_start is not None and index >= floorplan_start else "photo"

            images.append({
                "url": url,
                "category": category,
                "star": False
            })

        if images:
            return images

        og_image = self.safe_get(self.meta, ["helmet", "ogImage", "url"])
        if og_image:
            images.append({
                "url": og_image,
                "category": "photo",
                "star": False
            })

        return images

    async def agency(self):
        event = self.current_event()
        agency_data = event.get("agency") if isinstance(event, dict) else {}
        details = event.get("details") if isinstance(event, dict) else ""

        name = self.safe_get(agency_data, ["logo", "alt"])

        if not name and details and " by " in details:
            name = details.split(" by ", 1)[-1].strip()

        logo = self.image_url(self.safe_get(agency_data, ["logo"], {}))

        try:
            if logo:
                logo = await gcs.upload_images_to_gcs(
                    logo,
                    name or "agency",
                    "properties/agency"
                )
            else:
                logo = None
        except Exception:
            logo = None

        profile_url = None
        for link in event.get("linksV2") or []:
            if isinstance(link, dict) and link.get("title") == "Agency listing":
                profile_url = link.get("url")
                break

        return {
            "agencyId": self.safe_get(self.meta, ["tracking", "agentsContext", "data", "agents", 0, "agency_id"]),
            "address": None,
            "name": name,
            "description": None,
            "logo": logo,
            "profileUrl": profile_url,
            "contactDetails": None,
            "isArchived": False,
        }

    async def agents(self):
        agents = []
        event = self.current_event()
        agent_data = event.get("agent") if isinstance(event, dict) else {}

        if not isinstance(agent_data, dict) or not agent_data.get("name"):
            return agents

        photo = self.image_url(agent_data.get("photo"))

        try:
            if photo:
                photo = await gcs.upload_images_to_gcs(
                    photo,
                    agent_data.get("name") or "agent",
                    "properties/agent"
                )
            else:
                photo = None
        except Exception:
            photo = None

        first_name, last_name = self.split_name(agent_data.get("name"))

        agents.append({
            "agentId": self.safe_get(self.meta, ["tracking", "agentsContext", "data", "agents", 0, "agent_id"]),
            "firstName": first_name,
            "lastName": last_name,
            "isActiveProfilePage": True,
            "phoneNumber": None,
            "profileUrl": None,
            "photo": photo,
            "title": agent_data.get("jobTitle")
        })

        return agents

    def normalize_to_sqm(self, data):
        if not isinstance(data, dict):
            return {
                "totalArea": 0,
                "unit": "sqM"
            }

        raw_value = data.get("totalArea", 0)
        raw_unit = data.get("unit", "sqM")

        try:
            value = float(str(raw_value).replace(",", "").strip())
        except Exception:
            value = 0

        unit = str(raw_unit or "sqM").strip().lower()
        factor = UNIT_TO_SQM.get(unit, 1)

        return {
            "totalArea": value * factor,
            "unit": "sqM"
        }

    def parse_area(self, text):
        text = str(text or "").strip().replace(",", "")
        match = re.search(r"([\d.]+)\s*([a-zA-Z². ]+)?", text)

        if not match:
            return {
                "totalArea": 0,
                "unit": "sqM"
            }

        return self.normalize_to_sqm({
            "totalArea": match.group(1),
            "unit": (match.group(2) or "sqM").strip()
        })

    def price_to_float(self, price):
        if not price:
            return None

        price = str(price).lower()

        multiplier = 1
        if re.search(r"\d\s*k\b", price):
            multiplier = 1000
        elif re.search(r"\d\s*m\b", price):
            multiplier = 1000000

        trimmed = re.sub(r"[^\d,.]+", "", price)

        if not trimmed:
            return None

        try:
            trimmed = trimmed.replace(",", "")
            value = float(trimmed)
            return value * multiplier
        except Exception:
            return None

    def clean_text(self, value):
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def sold_date_from_text(self, text):
        text = self.clean_text(text)
        match = re.search(r"\bSold\s+(.+)$", text, flags=re.IGNORECASE)

        if match:
            return match.group(1).strip()

        # Newer timeline markup omits the "Sold" prefix, for example
        # "26 Mar 2026 by Ray White Rural - Guyra/Armidale".
        match = re.search(
            r"\b(\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
            r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
            r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{4})\b",
            text,
            flags=re.IGNORECASE,
        )

        return match.group(1) if match else None

    def format_sold_date(self, value):
        value = self.clean_text(value)

        if not value:
            return None

        for date_format in (
            "%d %b %Y",
            "%d %B %Y",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(value, date_format).isoformat()
            except ValueError:
                continue

        return value

    def text_from_html_fragment(self, fragment):
        fragment = re.sub(r"<!--.*?-->", " ", fragment or "", flags=re.DOTALL)
        fragment = re.sub(r"<[^>]+>", " ", fragment)
        return self.clean_text(html_lib.unescape(fragment))

    def first_class_text_from_fragment(self, fragment, class_part):
        pattern = (
            r'<(?P<tag>[a-zA-Z0-9]+)[^>]*class="[^"]*'
            + re.escape(class_part)
            + r'[^"]*"[^>]*>(?P<body>.*?)</(?P=tag)>'
        )
        match = re.search(pattern, fragment or "", flags=re.DOTALL)

        if not match:
            return ""

        return self.text_from_html_fragment(match.group("body"))

    def timeline_sold_history_from_html(self):
        historysale = []

        for match in re.finditer(
            r'<article[^>]*class="[^"]*PropertyTimelineEvent__TimelineEventWrapper[^"]*"[^>]*>(.*?)</article>',
            self.html,
            flags=re.DOTALL,
        ):
            article = match.group(1)
            badge_text = self.first_class_text_from_fragment(article, "styles__Content")

            if badge_text.lower() != "sold":
                continue

            title_text = self.first_class_text_from_fragment(
                article,
                "PropertyTimelineEvent__TitleWrapper"
            )
            details_text = self.first_class_text_from_fragment(
                article,
                "PropertyTimelineEvent__EventDetails"
            )
            sold_date = self.sold_date_from_text(details_text)
            sold_price = self.price_to_float(title_text)

            item = {
                "soldDate": self.format_sold_date(sold_date),
                "soldPrice": int(sold_price) if sold_price is not None else None,
            }

            historysale.append(item)

        return historysale

    def timeline_sold_history_from_dom(self):
        if not hasattr(self.soup, "select_one"):
            return self.timeline_sold_history_from_html()

        timeline = self.soup.select_one(
            '[class*="PropertyTimelineEvents__TimelineBody"], .coBZHX'
        )

        if not timeline:
            return []

        historysale = []

        for article in timeline.select('[class*="PropertyTimelineEvent__TimelineEventWrapper"]'):
            badge = article.select_one('[class*="styles__Content"]')
            badge_text = self.clean_text(badge.get_text(" ", strip=True) if badge else "")

            if badge_text.lower() != "sold":
                continue

            title = article.select_one('[class*="PropertyTimelineEvent__TitleWrapper"]')
            details = article.select_one('[class*="PropertyTimelineEvent__EventDetails"]')
            title_text = self.clean_text(title.get_text(" ", strip=True) if title else "")
            details_text = self.clean_text(details.get_text(" ", strip=True) if details else "")

            sold_date = self.sold_date_from_text(details_text)
            sold_price = self.price_to_float(title_text)

            item = {
                "soldDate": self.format_sold_date(sold_date),
                "soldPrice": int(sold_price) if sold_price is not None else None,
            }

            historysale.append(item)

        return historysale

    def timeline_sold_history_from_meta(self):
        historysale = []

        for event in self.timeline_events():
            badge_text = (
                self.safe_get(event, ["badgeV3", "text"])
                or self.safe_get(event, ["badge", "text"])
                or ""
            )

            if str(badge_text).strip().lower() != "sold":
                continue

            sold_date = self.sold_date_from_text(event.get("details"))
            sold_price = self.price_to_float(event.get("titleV2"))

            item = {
                "soldDate": self.format_sold_date(sold_date),
                "soldPrice": int(sold_price) if sold_price is not None else None,
            }

            historysale.append(item)

        return historysale

    def sold_history(self):
        return self.timeline_sold_history_from_dom() or self.timeline_sold_history_from_meta()

    def cal_expected_price(self, option):
        option = option or ""
        prices = re.findall(r"\$[\d,.]+\s*[kmKM]?", option)

        if not prices:
            return self.price_to_float(option)

        values = [self.price_to_float(price) for price in prices]
        values = [value for value in values if value is not None]

        if not values:
            return None

        return int(sum(values) / len(values))

    def features(self, garage):
        features = {
            "garage": garage,
            "floorNo": None,
            "indoorFeatures": [],
            "outdoorAmenities": [],
            "basement": None,
            "floorCovering": [],
            "roof": [],
            "view": [],
            "appliances": [],
            "buildingAmenities": [],
            "displayAddress": "fullAddress",
        }

        indoor_titles = {
            "air conditioning",
            "built in robes",
            "dishwasher",
            "living areas",
            "toilets",
        }
        outdoor_titles = {
            "carport spaces",
            "shed",
            "solar panels",
        }

        feature_items = []
        feature_items.extend(self.safe_get(self.meta, ["propertyFeaturesV2", "keyFeatures", "itemsV2"], []) or [])
        feature_items.extend(self.safe_get(self.meta, ["propertyFeaturesV2", "additionalFeatures", "itemsV2"], []) or [])

        for item in feature_items:
            if not isinstance(item, dict):
                continue

            title = item.get("title")
            value = item.get("valueWithUnit")
            label = f"{title}: {value}" if value else title
            title_key = str(title or "").lower()

            if title_key in indoor_titles:
                features["indoorFeatures"].append(label)
            elif title_key in outdoor_titles:
                features["outdoorAmenities"].append(label)

        return features

    async def getPriceInfo(self, data_type):
        historysale = self.sold_history()

        event = self.current_event()
        status_text = self.safe_get(self.meta, ["header", "status", "badgeV2", "text"], "")
        status_key = str(status_text or "").strip().lower()
        pricing_options = event.get("titleV2") if isinstance(event, dict) else None

        if not pricing_options:
            pricing_options = self.safe_get(self.meta, ["valueEstimatesV2", "estimates", "sale", "price", "valueWithUnit"])

        expected_price = self.cal_expected_price(pricing_options)

        status = "for-sale"
        listing_option = "sale"
        authority = "for-sale"
        sold_date_info = ""

        if "under offer" in status_key:
            status = "under-offer"
            authority = "under-offer"
            expected_price = None
        elif "sold" in status_key or status_key in {"off market", "off-market"}:
            status = "sold"
            listing_option = "sold"
            authority = "sold"
            sold_date_info = event.get("details", "") if isinstance(event, dict) else ""

            if not historysale:
                sold_date = self.sold_date_from_text(sold_date_info) or sold_date_info
                item = {
                    "soldDate": self.format_sold_date(sold_date),
                    "soldPrice": int(expected_price) if expected_price is not None else None,
                }

                historysale.append(item)
        elif "rent" in status_key or data_type in ("rental", "rent"):
            status = "for-rent"
            listing_option = "rent"
            authority = "rent"

        pricing = {
            "waterBillPeriod": "monthly",
            "councilBill": "",
            "pricingOptions": pricing_options,
            "authority": authority,
            "priceIncludes": [""]
        }

        saleInfo = {
            "expectedPrice": expected_price,
            "listingOption": listing_option,
            "pricing": pricing,
            "status": status,
            "soldDateInfo": sold_date_info
        }

        return historysale, saleInfo

    def feature_value(self, title):
        items = self.safe_get(self.meta, ["header", "propertyFeatures", "items"], []) or []

        for item in items:
            if isinstance(item, dict) and item.get("title") == title:
                return item.get("valueWithUnit")

        items = self.safe_get(self.meta, ["propertyFeaturesV2", "keyFeatures", "itemsV2"], []) or []

        for item in items:
            if isinstance(item, dict) and item.get("title") == title:
                return item.get("valueWithUnit")

        return None

    def int_value(self, value):
        try:
            return int(float(str(value).replace(",", "").strip()))
        except Exception:
            return 0

    def address_parts(self):
        seo_schema = self.safe_get(self.meta, ["helmet", "seoSchemas", 0])

        if seo_schema:
            try:
                schema = json.loads(seo_schema)
                address = schema.get("address") or {}
                street = address.get("streetAddress") or ""
                suburb = address.get("addressLocality") or ""
                state = address.get("addressRegion") or ""
                postcode = address.get("postalCode") or ""
                return street, suburb, state, postcode
            except Exception:
                pass

        part1 = self.safe_get(self.meta, ["header", "pageTitleByPart", "part1"], "")
        part2 = self.safe_get(self.meta, ["header", "pageTitleByPart", "part2"], "")
        match = re.match(r"(.+),\s*([A-Z]{2,3})\s+(\d{4})$", part2 or "")

        if match:
            return part1, match.group(1), match.group(2), match.group(3)

        title = self.safe_get(self.meta, ["helmet", "title"], "")
        title = re.sub(r"\s+-\s+property\.com\.au.*$", "", title or "")
        match = re.match(r"(.+),\s*(.+)\s+([A-Z]{2,3})\s+(\d{4})$", title)

        if match:
            return match.group(1), match.group(2), match.group(3), match.group(4)

        return part1, "", "", ""

    def description(self):
        description = self.safe_get(self.meta, ["propertyDescriptionV2", "description"], [])

        if isinstance(description, list):
            return "".join(
                item.get("text", "")
                for item in description
                if isinstance(item, dict)
            ).strip()

        if isinstance(description, str):
            return description

        return self.safe_get(self.meta, ["helmet", "description"])

    def propertysale(self):
        prosale = {}

        property_type = (
            self.feature_value("Property type")
            or self.feature_value("Type")
            or self.safe_get(self.meta, ["tracking", "propertyContext", "data", "property_type"])
        )

        bed = self.int_value(
            self.feature_value("Bedrooms")
            or self.safe_get(self.meta, ["tracking", "propertyContext", "data", "bedrooms"])
        )
        bath = self.int_value(
            self.feature_value("Bathrooms")
            or self.safe_get(self.meta, ["tracking", "propertyContext", "data", "bathrooms"])
        )
        garage = self.int_value(
            self.feature_value("Garage spaces")
            or self.feature_value("Carport spaces")
        )

        area_text = self.feature_value("Land size")
        area = self.parse_area(area_text)

        coordinates_data = self.safe_get(self.meta, ["propertyMap", "pins", 0, "coordinates"], {})
        coordinates = {
            "lat": coordinates_data.get("latitude") if isinstance(coordinates_data, dict) else None,
            "lon": coordinates_data.get("longitude") if isinstance(coordinates_data, dict) else None
        }

        street, suburb, state, postcode = self.address_parts()
        city = self.get_city(
            state_input=state,
            suburb_input=suburb,
            lookup_dict=lookup
        )

        event = self.current_event()
        price = event.get("titleV2") if isinstance(event, dict) else None

        if not price:
            price = self.safe_get(self.meta, ["valueEstimatesV2", "estimates", "sale", "price", "valueWithUnit"])

        expected_price = self.cal_expected_price(price)
        features = self.features(garage=garage)
        title = self.safe_get(self.meta, ["helmet", "title"])

        prosale["architecturalStyle"] = "None"
        prosale["area"] = area
        prosale["bed"] = bed
        prosale["bath"] = bath
        prosale["coordinates"] = coordinates
        prosale["constructionYear"] = self.safe_get(self.meta, ["tracking", "propertyContext", "data", "year_built"], "N/A") or "N/A"
        prosale["expectedPrice"] = expected_price
        prosale["street"] = street
        prosale["state"] = state
        prosale["suburb"] = suburb
        prosale["city"] = city
        prosale["postcode"] = postcode
        prosale["propertyType"] = property_type
        prosale["title"] = title
        prosale["description"] = self.description()
        prosale["features"] = features
        prosale["structuralRemodelYear"] = "N/A"
        prosale["recommended"] = False
        prosale["published"] = False
        prosale["realestate_url"] = self.url

        return prosale

    def create_slug(self):
        slug = urlparse(self.url).path.strip("/").split("/")[-1]

        if not slug:
            return ""

        return slug

    async def Mapping(self):
        new_slug = self.create_slug()

        historysale, saleInfo = await self.getPriceInfo(self.datatype)
        prosale = self.propertysale()
        agents = await self.agents()
        agency = await self.agency()
        images = await self.images()

        for temp in images:
            i_url = temp.get("url")

            if not i_url:
                continue

            try:
                listing_option = saleInfo.get("listingOption")

                if listing_option == "sold":
                    folder = "properties/prosold"
                elif listing_option == "rent":
                    folder = "properties/prorent"
                elif listing_option == "leased":
                    folder = "properties/proleased"
                else:
                    folder = "properties/prosale"

                temp["url"] = await gcs.upload_images_to_gcs(
                    i_url,
                    prosale.get("street") or "property",
                    folder
                )

            except Exception:
                temp["url"] = i_url

        contactInfo = []

        for agent in agents:
            contactInfo.append({
                "firstName": agent.get("firstName"),
                "lastName": agent.get("lastName"),
                "phoneNumber": agent.get("phoneNumber"),
                "photo": agent.get("photo")
            })

        prosale["contactInfo"] = contactInfo
        prosale["images"] = images
        prosale["soldDateInfo"] = saleInfo.get("soldDateInfo")
        prosale["listingOption"] = saleInfo.get("listingOption")
        prosale["pricing"] = saleInfo.get("pricing")
        prosale["status"] = saleInfo.get("status")
        prosale["historysale"] = historysale
        prosale["slug"] = new_slug

        return {
            "historySale": historysale,
            "saleInfo": saleInfo,
            "agents": agents,
            "agency": agency,
            "images": images,
            "propertyforsale": prosale
        }
