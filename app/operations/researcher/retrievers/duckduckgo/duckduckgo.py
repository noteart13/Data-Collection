from ddgs import DDGS
from urllib.parse import urlparse

from  ..utils import check_pkg
class Duckduckgo:
    """
    Duckduckgo API Retriever
    """
    def __init__(self, query, query_domains=None):
        check_pkg('ddgs')
        from ddgs import DDGS
        self.ddg = DDGS()
        self.query = query
        if isinstance(query_domains, str):
            query_domains = [query_domains]
        self.query_domains = query_domains or None

    def _query_with_domains(self):
        if not self.query_domains:
            return self.query

        site_filters = " ".join(f"site:{domain}" for domain in self.query_domains)
        return f"{self.query} {site_filters}"

    def _is_allowed_domain(self, result):
        if not self.query_domains:
            return True

        href = result.get("href") or result.get("url") or ""
        netloc = urlparse(href).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]

        return any(
            netloc == domain.lower().removeprefix("www.")
            or netloc.endswith(f".{domain.lower().removeprefix('www.')}")
            for domain in self.query_domains
        )

    def search(self, max_results=5):
        """
        Performs the search
        :param query:
        :param max_results:
        :return:
        """
        try:
            search_response = self.ddg.text(
                self._query_with_domains(),
                region='au-en',
                max_results=max_results
            )
        except Exception as e:
            print(f"Error: {e}. Failed fetching sources. Resulting in empty response.")
            search_response = []
        return [
            result
            for result in search_response
            if self._is_allowed_domain(result)
        ]
