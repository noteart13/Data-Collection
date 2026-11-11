import re
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
    """
    Safely get nested dict/list data.

    Example:
        safe_get(meta, ["details", "listing", "media"], {})
    """
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


def extract_metadata(soup):
    """
    Extract realestate.com.au ArgonautExchange metadata safely.
    """
    if soup is None:
        return {}

    script = soup.find("script", string=re.compile("ArgonautExchange"))

    if not script or not script.string:
        return {}

    match = re.search(
        r"window\.ArgonautExchange\s*=\s*(\{.*?\});",
        script.string,
        re.S
    )

    if not match:
        return {}

    try:
        argonaut = json5.loads(match.group(1))
    except Exception:
        return {}

    cache_raw = safe_get(
        argonaut,
        ["resi-property_listing-experience-web", "urqlClientCache"]
    )

    if not cache_raw:
        return {}

    try:
        cache = json5.loads(cache_raw)
    except Exception:
        return {}

    try:
        first_value = next(iter(cache.values()))
        data_raw = first_value.get("data")
        return json5.loads(data_raw) if data_raw else {}
    except Exception:
        return {}


class PropertyMeta:
    def __init__(self, html, url, datatype):
        self.url = url
        self.html = html or ""
        self.datatype = datatype

        self.soup = BeautifulSoup(self.html, "html.parser")
        self.meta = extract_metadata(self.soup)

        try:
            with open("meta.json", "w", encoding="utf-8") as file:
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

        key = (state_input.lower(), suburb_input.lower())
        return lookup_dict.get(key)

    async def images(self):
        images = []

        results = self.safe_get(
            self.meta,
            ["details", "listing", "media"],
            {}
        )

        if not isinstance(results, dict):
            return images

        for key, value in results.items():
            if key in ["images", "floorplans"]:
                category = "photo" if key == "images" else "floorplan"

                for item in value or []:
                    url = item.get("templatedUrl", "") if isinstance(item, dict) else ""

                    if url:
                        url = url.replace("{size}", "1200x880")

                    images.append({
                        "url": url,
                        "category": category,
                        "star": False
                    })

        return images

    async def agency(self):
        result = self.safe_get(
            self.meta,
            ["details", "listing", "listingCompany"],
            {}
        )

        if not isinstance(result, dict):
            result = {}

        logo = self.safe_get(result, ["media", "logo", "templatedUrl"])

        try:
            if logo:
                logo_url = logo.format(size="340x64")
                logo = await gcs.upload_images_to_gcs(
                    logo_url,
                    result.get("name") or "agency",
                    "properties/agency"
                )
            else:
                logo = None
        except Exception:
            logo = None

        agency = {
            "agencyId": result.get("id"),
            "address": self.safe_get(result, ["address", "display", "fullAddress"]),
            "name": result.get("name"),
            "description": result.get("description"),
            "logo": logo,
            "profileUrl": self.safe_get(result, ["_links", "canonical", "href"]),
            "contactDetails": result.get("businessPhone"),
            "isArchived": False,
        }

        return agency

    async def agents(self):
        agents = []

        results = self.safe_get(
            self.meta,
            ["details", "listing", "listers"],
            []
        )

        for result in results or []:
            if not isinstance(result, dict):
                continue

            photo = self.safe_get(result, ["photo", "templatedUrl"])

            try:
                if photo:
                    photo_url = photo.format(size="200x200")
                    photo = await gcs.upload_images_to_gcs(
                        photo_url,
                        result.get("name") or "agent",
                        "properties/agent"
                    )
                else:
                    photo = None
            except Exception:
                photo = None

            profile_url = self.safe_get(result, ["_links", "canonical", "href"])
            first_name, last_name = self.split_name(result.get("name"))

            agent = {
                "agentId": result.get("id"),
                "firstName": first_name,
                "lastName": last_name,
                "isActiveProfilePage": True,
                "phoneNumber": self.safe_get(result, ["phoneNumber", "display"]),
                "profileUrl": profile_url,
                "photo": photo,
                "title": result.get("jobTitle")
            }

            agents.append(agent)

        return agents

    def normalize_to_sqm(self, data):
        """
        Convert area dict to square meters.
        Example:
            {'totalArea': '106', 'unit': 'm²'}
        """
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

    def price_to_float(self, price):
        if not price:
            return None

        price = str(price)

        trimmer = re.compile(r"[^\d,.]+")
        trimmed = trimmer.sub("", price or "")

        if not trimmed:
            return None

        try:
            if trimmed.isdigit() and int(trimmed) > 99:
                trimmed += ",000"

            if len(trimmed) > 5:
                pos1 = trimmed.find(".")
                pos2 = trimmed.find(",")

                if pos1 != -1 and pos2 != -1 and pos1 < pos2:
                    trimmed = trimmed.replace(".", "")

                trimmed = trimmed.replace(",", "")
                return float(trimmed)

            pos1 = trimmed.find(".")
            pos2 = trimmed.find(",")

            if pos1 < pos2 and pos1 != -1:
                trimmed = trimmed.replace(",", "")
            elif pos2 < pos1 and pos2 != -1:
                trimmed = trimmed.replace(".", "")

            trimmed = trimmed.replace(",", ".").rstrip(".")

            return float(trimmed) * 1000000

        except Exception:
            return None

    def cal_expected_price(self, option):
        option = option or ""

        phones = re.findall(r"(?:0|61|\+61)[2345678]\d{8}", option)

        for phone in phones:
            option = option.replace(phone, "")

        pos = option.rfind("$")

        if pos == -1:
            return self.price_to_float(option)

        min_price = option[:pos]
        max_price = option[pos:]

        if "$" not in min_price:
            min_price = max_price

        min_price = self.price_to_float(min_price)
        max_price = self.price_to_float(max_price)

        if min_price is None and max_price is None:
            return None

        if min_price is None:
            return int(max_price)

        if max_price is None:
            return int(min_price)

        return int(float((min_price + max_price) / 2))

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

        feature = self.safe_get(
            self.meta,
            [
                "components",
                "details",
                "components",
                "PropertyFeatureComponent",
                "component",
                "allPropertyFeatures",
                "propertyFeatureCategories"
            ],
            []
        )

        for result in feature or []:
            if not isinstance(result, dict):
                continue

            title = result.get("title")
            items = result.get("propertyFeatureItems") or []

            if title == "Indoor features":
                for item in items:
                    label = item.get("displayLabel") if isinstance(item, dict) else None
                    if label:
                        features["indoorFeatures"].append(label)

            elif title == "Outdoor features":
                for item in items:
                    label = item.get("displayLabel") if isinstance(item, dict) else None
                    if label:
                        features["outdoorAmenities"].append(label)

        return features

    async def getPriceInfo(self, data_type):
        historysale = {
            "soldDate": None,
            "soldPrice": None,
            "agencyId": None
        }

        saleInfo = {
            "expectedPrice": None,
            "listingOption": None,
            "pricing": {
                "waterBillPeriod": "monthly",
                "councilBill": "",
                "pricingOptions": None,
                "authority": None,
                "priceIncludes": [""]
            },
            "status": None,
            "soldDateInfo": ""
        }

        if data_type != "salesold":
            return historysale, saleInfo

        price_tag = self.soup.find(
            "span",
            class_="property-price property-info__price"
        )

        pricing_options = self.safe_text(price_tag)
        expected_price = self.price_to_float(pricing_options)

        status_tag = self.soup.find(
            "span",
            class_="styles__Content-sc-1cced9e-1 hVISgY"
        )

        status_text = self.safe_text(status_tag)

        sold_date_info = ""
        sold_date = None
        sold_price = None
        status = "for-sale"
        listing_option = "sale"
        authority = "for-sale"

        if status_text == "Under offer":
            status = "under-offer"
            listing_option = "sale"
            authority = "under-offer"
            expected_price = None

        elif status_text == "Sold":
            price_info_tag = self.soup.find(
                "div",
                class_="property-info__middle-content"
            )

            price_info = []
            if price_info_tag:
                price_info = [
                    span.get_text(strip=True)
                    for span in price_info_tag.find_all("span")
                ]

            if len(price_info) >= 2:
                sold_price = self.price_to_float(price_info[0])
                sold_date = price_info[1]
                sold_date_info = price_info[1]
                pricing_options = price_info[0]

            status = "sold"
            listing_option = "sold"
            authority = "sold"

        agency_id = self.safe_get(
            self.meta,
            ["details", "listing", "listingCompany", "id"]
        )

        pricing = {
            "waterBillPeriod": "monthly",
            "councilBill": "",
            "pricingOptions": pricing_options,
            "authority": authority,
            "priceIncludes": [""]
        }

        historysale = {
            "soldDate": sold_date,
            "soldPrice": sold_price,
            "agencyId": agency_id
        }

        saleInfo = {
            "expectedPrice": expected_price,
            "listingOption": listing_option,
            "pricing": pricing,
            "status": status,
            "soldDateInfo": sold_date_info
        }

        return historysale, saleInfo

    def propertysale(self):
        prosale = {}

        bath = 0
        bed = 0
        garage = 0

        property_type = self.safe_get(
            self.meta,
            ["details", "listing", "propertyType", "display"]
        )

        bedbathcar = self.safe_get(
            self.meta,
            ["details", "listing", "generalFeatures"],
            {}
        )

        if not isinstance(bedbathcar, dict):
            bedbathcar = {}

        sizes = self.safe_get(
            self.meta,
            ["details", "listing", "propertySizes"],
            {}
        )

        building = sizes.get("building") or {}
        land = sizes.get("land") or {}

        area = {
            "totalArea": (
                building.get("displayValue")
                or land.get("displayValue")
                or 0
            ),
            "unit": (
                safe_get(building, ["sizeUnit", "displayValue"])
                or safe_get(land, ["sizeUnit", "displayValue"])
                or "sqM"
            )
        }

        for key in bedbathcar.keys():
            if key == "bathrooms":
                bath = safe_get(bedbathcar, [key, "value"], 0)
            elif key == "bedrooms":
                bed = safe_get(bedbathcar, [key, "value"], 0)
            elif key == "parkingSpaces":
                garage = safe_get(bedbathcar, [key, "value"], 0)

        geo_code = self.safe_get(
            self.meta,
            ["details", "listing", "address", "display", "geocode"],
            {}
        )

        coordinates = {
            "lat": geo_code.get("latitude") if isinstance(geo_code, dict) else None,
            "lon": geo_code.get("longitude") if isinstance(geo_code, dict) else None
        }

        area = self.normalize_to_sqm(area)

        address_tag = self.soup.find("h1", class_="property-info-address")
        address = self.safe_text(address_tag, "")

        street = " ".join(filter(None, address.split(" ")))

        sub_state_postcode = street.split(" ")[-3:]

        suburb = ""
        state = ""
        postcode = ""

        if len(sub_state_postcode) >= 3:
            suburb = sub_state_postcode[0].replace(",", "")
            state = sub_state_postcode[1].replace(",", "")
            postcode = sub_state_postcode[2].replace(",", "")

        city = self.get_city(
            state_input=state,
            suburb_input=suburb,
            lookup_dict=lookup
        )

        price_tag = self.soup.find(
            "span",
            class_="property-price property-info__price"
        )
        price = self.safe_text(price_tag)

        title_tag = self.soup.find(
            "h2",
            class_="Text__Typography-sc-1103tao-0 gFHdmt"
        )
        title = self.safe_text(title_tag)

        description_tag = self.soup.find(
            "p",
            class_="property-description__content"
        )
        description = self.safe_text(description_tag)

        expected_price = self.price_to_float(price)
        features = self.features(garage=garage)

        prosale["architecturalStyle"] = "None"
        prosale["area"] = area
        prosale["bed"] = bed
        prosale["bath"] = bath
        prosale["coordinates"] = coordinates
        prosale["constructionYear"] = "N/A"
        prosale["expectedPrice"] = expected_price
        prosale["street"] = street
        prosale["state"] = state
        prosale["suburb"] = suburb
        prosale["city"] = city
        prosale["postcode"] = postcode
        prosale["propertyType"] = property_type
        prosale["title"] = title
        prosale["description"] = description
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

        parts = slug.split("-")
        number = parts[-1]

        try:
            parts[-1] = format(int(number), "x")
        except Exception:
            return slug

        return "-".join(parts)

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
