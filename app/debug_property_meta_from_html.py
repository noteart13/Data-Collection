import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_MAPPING_PATH = Path(__file__).resolve().parents[1] / "suburb_city_mapping_v1.json"
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent / "data" / "buyer_agent_search_results.jsonl"

EXCLUDED_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
    "realestate.com.au",
    "domain.com.au",
    "yellowpages.com.au",
    "oneflare.com.au",
    "hipages.com.au",
)


def load_locations(mapping_path, states=None, cities=None):
    """Load and de-duplicate suburb records from suburb_city_mapping_v1.json."""
    path = Path(mapping_path)
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array in {path}")

    state_filter = {state.upper() for state in (states or [])}
    city_filter = {city.casefold() for city in (cities or [])}
    required_fields = ("city", "suburb", "state", "postcode")
    locations = []
    seen = set()

    for index, item in enumerate(data):
        if not isinstance(item, dict) or any(not item.get(field) for field in required_fields):
            raise ValueError(f"Invalid location at index {index}: {item!r}")

        location = {field: str(item[field]).strip() for field in required_fields}
        location["state"] = location["state"].upper()

        if state_filter and location["state"] not in state_filter:
            continue
        if city_filter and location["city"].casefold() not in city_filter:
            continue

        key = (location["suburb"].casefold(), location["state"], location["postcode"])
        if key not in seen:
            seen.add(key)
            locations.append(location)

    return locations


def build_search_query(location, service_term="buyers agent"):
    """Build a high-precision DuckDuckGo query for one Australian suburb."""
    exclusions = " ".join(f"-site:{domain}" for domain in EXCLUDED_DOMAINS)
    return (
        f'"{service_term}" "{location["suburb"]}" '
        f'{location["state"]} {location["postcode"]} Australia {exclusions}'
    )


def get_domain(url):
    domain = urlparse(url).netloc.lower()
    return domain.removeprefix("www.")


def search_location(location, max_results=10):
    """Search buyer agents first, then buyer advocates when no result is found."""
    from operations.researcher.retrievers.duckduckgo.duckduckgo import Duckduckgo

    queries = [build_search_query(location, "buyers agent")]
    results = Duckduckgo(query=queries[0]).search(max_results=max_results)

    if not results:
        fallback_query = build_search_query(location, "buyers advocate")
        queries.append(fallback_query)
        results = Duckduckgo(query=fallback_query).search(max_results=max_results)

    unique_results = []
    seen_urls = set()
    for result in results:
        url = result.get("href") or result.get("url")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        unique_results.append(
            {
                "title": result.get("title"),
                "url": url,
                "domain": get_domain(url),
                "snippet": result.get("body"),
            }
        )

    return {"location": location, "queries": queries, "results": unique_results}


def append_jsonl(path, record):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Find Australian buyer-agent websites by suburb with DuckDuckGo."
    )
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--state", action="append", help="State code; repeat for multiple states.")
    parser.add_argument("--city", action="append", help="Exact city/LGA; repeat for multiple cities.")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of locations to process. Use 0 for every matching location.",
    )
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between locations.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated queries without calling DuckDuckGo.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.offset < 0 or args.limit < 0 or args.max_results < 1 or args.delay < 0:
        raise SystemExit("offset/delay must be >= 0, limit >= 0, and max-results >= 1")

    locations = load_locations(args.mapping, states=args.state, cities=args.city)
    end = None if args.limit == 0 else args.offset + args.limit
    selected_locations = locations[args.offset:end]

    print(
        f"Loaded {len(locations)} matching locations; "
        f"processing {len(selected_locations)} from offset {args.offset}."
    )

    for number, location in enumerate(selected_locations, start=1):
        query = build_search_query(location)
        if args.dry_run:
            print(query)
            continue

        record = search_location(location, max_results=args.max_results)
        append_jsonl(args.output, record)
        print(
            f"[{number}/{len(selected_locations)}] "
            f'{location["suburb"]}, {location["state"]}: '
            f'{len(record["results"])} result(s)'
        )
        if number < len(selected_locations) and args.delay:
            time.sleep(args.delay)


if __name__ == "__main__":
    main()
