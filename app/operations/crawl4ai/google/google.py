
import os
import requests
import json
class GoogleSearch:
    """
    Tavily API Retriever
    """
    def __init__(self, query, headers=None):
        """
        Initializes the TavilySearch object
        Args:
            query:
        """
        self.query = query
        self.headers = headers or {}
        self.api_key = self.headers.get("google_api_key") or os.getenv("GOOGLE_API_KEY", "")
        self.cx_key = self.headers.get("google_cx_key") or os.getenv("GOOGLE_CX_KEY", "")
    def get_api_key(self):
        """
        Gets the Google API key
        Returns:

        """
        # Get the API key
        try:
            api_key = os.environ["GOOGLE_API_KEY"]
        except:
            raise Exception("Google API key not found. Please set the GOOGLE_API_KEY environment variable. "
                            "You can get a key at https://developers.google.com/custom-search/v1/overview")
        return api_key

    def get_cx_key(self):
        """
        Gets the Google CX key
        Returns:

        """
        # Get the API key
        try:
            api_key = os.environ["GOOGLE_CX_KEY"]
        except:
            raise Exception("Google CX key not found. Please set the GOOGLE_CX_KEY environment variable. "
                            "You can get a key at https://developers.google.com/custom-search/v1/overview")
        return api_key
    def get_search(self, url):
        resp = requests.get(url)

        if resp is None:
            return
        try:
            search_results = json.loads(resp.text)
        except Exception:
            return
        if search_results is None:
            return

        results = search_results.get("items", [])
        search_results = []

        # Normalizing results to match the format of the other search APIs
        for result in results:
            # skip youtube results
            if "youtube.com" in result["link"]:
                continue
            #search_result = {
            #    "title": result["title"],
            #    "href": result["link"],
            #    "body": result["snippet"],
            #}
            search_result = {
                "href": result["link"],
            }
            search_results.append(search_result)
        return search_results
    def search(self, max_results=7):
        """
        Searches the query
        Returns:

        """
        """Useful for general internet search queries using the Google API."""
        print("Searching with query {0}...".format(self.query))
        url1 = f"https://www.googleapis.com/customsearch/v1?key={self.api_key}&cx={self.cx_key}&q={self.query}&start=1&num=10"
        #url2 = f"https://www.googleapis.com/customsearch/v1?key={self.api_key}&cx={self.cx_key}&q={self.query}&start=11&num=10"
        #url3 = f"https://www.googleapis.com/customsearch/v1?key={self.api_key}&cx={self.cx_key}&q={self.query}&start=21&num=10"
        #url4 = f"https://www.googleapis.com/customsearch/v1?key={self.api_key}&cx={self.cx_key}&q={self.query}&start=31&num=10"
        #url5 = f"https://www.googleapis.com/customsearch/v1?key={self.api_key}&cx={self.cx_key}&q={self.query}&start=41&num=10"
        #url6 = f"https://www.googleapis.com/customsearch/v1?key={self.api_key}&cx={self.cx_key}&q={self.query}&start=51&num=10"
        #url7 = f"https://www.googleapis.com/customsearch/v1?key={self.api_key}&cx={self.cx_key}&q={self.query}&start=61&num=10"
        #url8 = f"https://www.googleapis.com/customsearch/v1?key={self.api_key}&cx={self.cx_key}&q={self.query}&start=71&num=10"
        #url9 = f"https://www.googleapis.com/customsearch/v1?key={self.api_key}&cx={self.cx_key}&q={self.query}&start=81&num=10"
        #url10 = f"https://www.googleapis.com/customsearch/v1?key={self.api_key}&cx={self.cx_key}&q={self.query}&start=91&num=10"

        #url = f"https://www.googleapis.com/customsearch/v1/siterestrict?key={self.api_key}&cx={self.cx_key}&q={self.query}"



        search_results = []
        #for url in [url1, url2, url3, url4, url5, url6, url7, url8, url9, url10]:
        for url in [url1]:
            search_result = self.get_search(url)
            search_results.extend(search_result)
        return search_results
