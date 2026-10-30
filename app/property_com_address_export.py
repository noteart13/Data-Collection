import argparse
import ast
import csv
from html.parser import HTMLParser
import json
import os
import re
from typing import Any
from urllib.parse import unquote, urlsplit


AU_STATE_PATTERN = r"(?:ACT|NSW|NT|QLD|SA|TAS|VIC|WA)"
PROPERTY_ADDRESS_RE = re.compile(
    rf"""
    ^
    (?P<address>
        (?:
            \d+[A-Za-z]?/\d+[A-Za-z]? |
            \d+[A-Za-z]? |
            Lot\s+\d+[A-Za-z]? |
            Unit\s+\d+[A-Za-z]?
        )
        [^,\n]{{3,}},
        \s*
        [A-Za-z][A-Za-z .'\-]+
        \s+{AU_STATE_PATTERN}
        \s+\d{{4}}
    )
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)

PROPERTY_PATH_RE = re.compile(
    r"^/(?:act|nsw|nt|qld|sa|tas|vic|wa)/"
    r"[a-z0-9]+(?:-[a-z0-9]+)*-\d{4}/"
    r"[a-z0-9]+(?:-[a-z0-9]+)*/"
    r"[a-z0-9]+(?:-[a-z0-9]+)*-pid-\d+/$",
    re.IGNORECASE,
)


class _PropertyLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return

        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value)
                break


def extract_property_paths(html: str) -> list[str]:
    """Extract unique property.com.au property paths from an HTML document."""
    parser = _PropertyLinkParser()
    parser.feed(html)

    paths: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        parsed = urlsplit(href)
        if parsed.netloc and not parsed.netloc.lower().endswith("property.com.au"):
            continue

        path = unquote(parsed.path)
        if not path.endswith("/"):
            path += "/"
        if not PROPERTY_PATH_RE.fullmatch(path):
            continue

        normalized_path = path.lower()
        if normalized_path in seen:
            continue

        seen.add(normalized_path)
        paths.append(path)

    return paths


def extract_property_paths_from_file(input_html: str) -> list[str]:
    with open(input_html, "r", encoding="utf-8") as file:
        return extract_property_paths(file.read())


def extract_property_addresses(raw_content: str) -> list[str]:
    addresses: list[str] = []
    seen: set[str] = set()

    for line in raw_content.splitlines():
        cleaned_line = " ".join(line.strip().split())
        match = PROPERTY_ADDRESS_RE.match(cleaned_line)
        if not match:
            continue

        address = match.group("address")
        address_key = address.lower()
        if address_key in seen:
            continue

        seen.add(address_key)
        addresses.append(address)

    return addresses


def extract_addresses_from_results(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for result in results:
        source_url = result.get("url", "")
        title = result.get("title", "")
        raw_content = result.get("raw_content") or ""

        for address in extract_property_addresses(raw_content):
            row_key = (source_url, address.lower())
            if row_key in seen:
                continue

            seen.add(row_key)
            rows.append(
                {
                    "source_url": source_url,
                    "address": address,
                    "title": title,
                }
            )

    return rows


def save_addresses_to_csv(
    results: list[dict[str, Any]],
    output_csv: str = "property_addresses.csv",
) -> int:
    rows = extract_addresses_from_results(results)
    fieldnames = ["address"]

    with open(output_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({"address": row["address"]} for row in rows)

    return len(rows)


def load_results(input_file: str) -> list[dict[str, Any]]:
    with open(input_file, "r", encoding="utf-8") as f:
        text = f.read()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = ast.literal_eval(text)

    if not isinstance(data, list):
        raise ValueError("Input file must contain a list of scraper results.")

    return data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract property.com.au addresses from scraper results and save them to CSV."
    )
    parser.add_argument("input_file", help="Path to scraper results file, e.g. results.txt")
    parser.add_argument(
        "-o",
        "--output",
        default="property_addresses.csv",
        help="Output CSV path. Default: property_addresses.csv",
    )
    args = parser.parse_args()

    results = load_results(args.input_file)
    row_count = save_addresses_to_csv(results, args.output)
    print(f"Saved {row_count} addresses to {os.path.abspath(args.output)}")


if __name__ == "__main__":
    main()
