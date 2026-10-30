# Tavily API Retriever

# libraries
import os

import numpy as np
from tavily import TavilyClient
from duckduckgo_search import DDGS


class TavilySearch():
    """
    Tavily API Retriever
    """
    def __init__(self, query, headers=None, topic="for sale real estate"):
        """
        Initializes the TavilySearch object
        Args:
            query:
        """
        self.query = query
        self.headers = headers or {}
        self.api_key = self.get_api_key()
        self.client = TavilyClient(self.api_key)
        self.topic = topic

    def get_api_key(self):
        """
        Gets the Tavily API key
        Returns:

        """
        api_key = self.headers.get("tavily_api_key") or os.getenv("TAVILY_API_KEY", "")
        if not api_key:
            raise Exception("Tavily API key not found. Please set the TAVILY_API_KEY environment variable.")
        return api_key

    def search(self, max_results=50):
        """
        Searches the query
        Returns:

        """
        try:
            # Search the query
            results = self.client.search(self.query, search_depth="basic", max_results=max_results, topic=self.topic)
            sources = results.get("results", [])
            if not sources:
                raise Exception("No results found with Tavily API search.")
            # Return the results
            search_response = [{"href": obj["url"], "body": obj["content"]} for obj in sources]
        except Exception as e: # Fallback in case overload on Tavily Search API
            print(f"Error: {e}. Fallback to DuckDuckGo Search API...")
            try:
                ddg = DDGS()
                search_response = ddg.text(self.query, region='wt-wt', max_results=max_results)
            except Exception as e:
                print(f"Error: {e}. Failed fetching sources. Resulting in empty response.")
                search_response = []
        return search_response
from dotenv import load_dotenv

load_dotenv()

def remove_price(result):
    title = result['title']
    price_str = title.find("$")
    if price_str != -1:
        subtitle = title[price_str:]
        price_end = subtitle.find(" ")
        price = title[price_str:price_str+price_end]
        new_title = title.replace(price, '')
        title = new_title
        result['title'] = title
    return result

if __name__ == '__main__':
    # fine_tune_clip()
    # scrapy html
    #scrape_all_pro_meta()
    import torch
    address = '30 Bourke St'
    address = '550 Epsom Rd, Flemington VIC 3031'
    address = '21C Billyard Avenue, Elizabeth Bay, NSW 2011' # return {'href': 'https://www.domain.com.au/project/6369/regency-house-elizabeth-bay-nsw/'}
    address = '8 Ironside Street, St Lucia QLD 4067'
    address = '3/7 Bellevue Terrace, St Lucia QLD 4067' # fail
    address = '6/54 Durham Street, St Lucia QLD 4067' # found
    address = '11/1 Bonvale Lane, St Lucia QLD 4067' # found
    address = '4/51 Mitre Street, St Lucia QLD 4067' # found
    address = '76 Highland Terrace, St Lucia QLD 4067' # found
    address = '49 Ironside Street, St Lucia QLD 4067'
    address = 'all properties for sale in St Lucia QLD 4067'
    # Search based on suburb
    tality = TavilySearch(address)
    relts = tality.search()
    fst_results = [result for result in relts if 'domain.com.au' in result['href']]
    results = []
    for result in fst_results:
        if 'domain.com.au/property-profile' not in result['href'] and 'domain.com.au/building-profile' not in result['href']:
            result['title'] = result['title'].replace('Domain.com.au','')
            result['title'] = result['title'].replace('Domain', '')
            result['title'] = result['title'].replace('|', '')
            result['title'] = result['title'].replace('-', '')
            results.append(result)
    final_resutls = [remove_price(result) for result in results]
    from fuzzywuzzy import fuzz

    # Sample list of street names from another dataset

    street_addresses = [result['title'] for result in final_resutls]
    # Target address component (we are using street name)
    target_street_name = address  # Note the different spelling and space
    # Similarity threshold
    threshold = 80

    # Perform fuzzy matching
    matches = []
    match = {}
    for address, href in zip([result['title'] for result in final_resutls], [result['href'] for result in final_resutls]):
        similarity_score = fuzz.partial_ratio(address, target_street_name)
        if similarity_score >= threshold:
            threshold = similarity_score
            match['title'] = address
            match['href'] = href
            match['similarity_score'] = similarity_score
            matches.append((address, similarity_score))
    matches.sort(key=lambda x: x[1], reverse=True)
    # Display matched street name
    print("Target Street Name:", target_street_name)
    print("Matched Street Names:")
    for match in matches:
        print(f"{match[0]} (Similarity: {match[1]}%)")
    print("ok")

