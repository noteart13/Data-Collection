import os
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import json
from pathlib import Path
import warnings
import cloudinary
import numpy as np
# import time
from operations.crawl4ai import(
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    LLMExtractionStrategy,
    JsonCssExtractionStrategy,
    CrawlResult,
    DefaultMarkdownGenerator,
    PruningContentFilter,
    CacheMode,
    LLMConfig
)
from bs4 import BeautifulSoup
from collections import OrderedDict
import pandas as pd
import asyncio
from dotenv import load_dotenv

load_dotenv()
_c_cfg = {
    "cloud_name": os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    "api_key": os.getenv("CLOUDINARY_API_KEY", ""),
    "api_secret": os.getenv("CLOUDINARY_API_SECRET", "")
}
if os.getenv("CLOUDINARY_PROXY"):
    _c_cfg["api_proxy"] = os.getenv("CLOUDINARY_PROXY")
if _c_cfg["cloud_name"] and _c_cfg["api_key"]:
    cloudinary.config(**_c_cfg)

warnings.filterwarnings("ignore", message='Field "model_name" has conflict with protected namespace "model_".')

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

async def search_url(url):
    browser_config = BrowserConfig(headless=True, verbose=True)
    crawler = AsyncWebCrawler(config=browser_config)
    await crawler.start()
    crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.DISABLED,
                keep_attrs=["id", "class"],
                keep_data_attributes=True,
                delay_before_return_html=2,
            )
    result = await crawler.arun(url=url,config=crawler_config)
    await crawler.close()
    page_source = result.html
    soup = BeautifulSoup(page_source, "html.parser")
    search_results = soup.find_all("div")
    links = []
    for result in search_results:
        try:
            link = result.a["href"]
            if 'https://www.domain.com.au' in link:
                links.append(link)
        except:
            continue
    links = list(OrderedDict.fromkeys(links))
    return links
async def get_total_pages(url):
    config = CrawlerRunConfig(
        cache_mode=CacheMode.DISABLED,        
    )
    browser_config = BrowserConfig(
            use_persistent_context=False,  
            user_data_dir=None,  
            extra_args=[
                "--disable-cache",  
                "--disable-application-cache",  
                "--disable-offline-load-stale-cache",  
                "--disk-cache-size=0",  
                "--media-cache-size=0",  
                "--disable-extensions",  
                "--disable-plugins", 
                "--disable-dev-shm-usage", 
                "--memory-pressure-off",  
                "--max_old_space_size=512",  
                "--incognito",  
                "--no-first-run",  
                "--no-default-browser-check",  
                "--disable-default-apps",  
                "--disable-sync", 
                "--disable-translate",  
                "--disable-features=TranslateUI", 
                "--disable-ipc-flooding-protection",
                "--enable-http2",  # Explicitly enable HTTP/2 to avoid protocol errors
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",  # Mimic a real browser
            ],
            accept_downloads=False,
            downloads_path=None,
            storage_state=None,  
            cookies=[], 
            headless=True, 
            light_mode=True,  
        )
    crawler = AsyncWebCrawler(config=browser_config)
    await crawler.start()
    result = await crawler.arun(url=url,config=config)
    await crawler.close()
    soup = BeautifulSoup(result.html, "html.parser")
    script_tags = soup.find_all("script")

    for script in script_tags:
        if "digitalData" in script.text:
            try:
                json_text = script.text.split("var digitalData = ")[1].split(";")[0]
                data = json.loads(json_text)
                return data["page"]["pageInfo"]["search"]["resultsPages"]
            except Exception:
                continue

    return None  
async def sale_urls(url):
    browser_config = BrowserConfig(
            use_persistent_context=False,  
            user_data_dir=None,  
            extra_args=[
                "--disable-cache",  
                "--disable-application-cache",  
                "--disable-offline-load-stale-cache",  
                "--disk-cache-size=0",  
                "--media-cache-size=0",  
                "--disable-extensions",  
                "--disable-plugins", 
                "--disable-dev-shm-usage", 
                "--memory-pressure-off",  
                "--max_old_space_size=512",  
                "--incognito",  
                "--no-first-run",  
                "--no-default-browser-check",  
                "--disable-default-apps",  
                "--disable-sync", 
                "--disable-translate",  
                "--disable-features=TranslateUI", 
                "--disable-ipc-flooding-protection",
                "--enable-http2",  # Explicitly enable HTTP/2 to avoid protocol errors
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",  # Mimic a real browser
            ],
            accept_downloads=False,
            downloads_path=None,
            storage_state=None,  
            cookies=[], 
            headless=True, 
            light_mode=True,  
        )
    crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.DISABLED,
                keep_attrs=["id", "class"],
                keep_data_attributes=True,
                delay_before_return_html=2,
            )
    crawler = AsyncWebCrawler(config=browser_config)
    await crawler.start()
    result = await crawler.arun(url=url,config=crawler_config)
    await crawler.close()
    page_source = result.html
    import re
    links = result.links["internal"]
    property_pattern = re.compile(r"https://www\.domain\.com\.au/.+-\d{5,10}")  
    property_links = [link['href'] for link in links if property_pattern.match(link['href'])]
    links = []
    for result in property_links:
        link = result  # result is already the href string
        if link.startswith("https://www.domain.com.au/"):
            links.append(link)
    links = list(OrderedDict.fromkeys(links))
    return links

def get_page_url(urls):
    #urls = search_url(url)
    for url in urls:
        if 'sale' in url  and 'house' not in url:
            sale_url = url
    return sale_url

async def filter_sale_urls(urls):
    excluded_urls = ['sale','group','owners','news/','property-profile','suburb-profile'] # suburb Newstead
    prosale_urls = []
    for url in urls:
        if len(url) > len('https://www.domain.com.au/'): # exluding https://www.domain.com.au/
            for excluded_url in excluded_urls:
                found = url.find(excluded_url)
                if found != -1:
                    break # exclude url if excluded_url found in url for first time
            if found == -1: # take url if no excluded_urls found in url
                prosale_urls.append(url)
    return prosale_urls

class NumpyEncoder(json.JSONEncoder):
    """ Special json encoder for numpy types """
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return json.JSONEncoder.default(self, obj)


async def scraping_by_suburb(search_keyword, name_csv, link, status):
    # if link: # in case name search does not work we manuualy put the link sale it is ok for sate since we have only 5 states
    #     sale_url = link
    # else:
    #     search_url_sale = "https://www.google.com/search?q=" + search_keyword
    #     urls = search_url(search_url_sale) # get all domain links
    #     sale_url = get_page_url(urls) # get for all property for sale links
    suburb = os.path.splitext(os.path.basename(name_csv))[0]
    suburb = suburb.split('/')[-1].replace('.csv', '').lower().replace('_', '-').replace(' ', '-').replace('suburb-city-mapping-v1-qld-run\\', '')
    print("Suburb:",suburb)
    sale_url = f'https://www.domain.com.au/{status}/{suburb}/'
    print(f'Manualy assign sale_url: {sale_url}')
    
    # scraping from page 1 to page n
    prosale_urls = []
    header = True
    mode = 'w'
    total_page = await get_total_pages(sale_url)
    print(total_page)
    for i in range(1,total_page+1):
        print('--------------------------------------------------------------')
        print("scraping page" + str(i))
        page_url = sale_url+'?page={}'.format(i) # scrape address for sale page by page
        links = await sale_urls(page_url) # get all domain links in this page
        # time.sleep(30)
        await asyncio.sleep(30)
        page_urls = await filter_sale_urls(links) # get all property for sale links in this page
        print()
        print('page_urls:', page_urls)
        print()
        if len(page_urls) == 0:
            break
        pages = ['Page_{}'.format(i)]*len(page_urls)
        json_urls = {"SaleURL": page_urls, "Page": pages}
        df = pd.DataFrame(data=json_urls)
        df.to_csv(name_csv,
                  mode=mode, sep=',', encoding='utf-8', header=header, index=True)
        prosale_urls.extend(page_urls)
        header = False
        mode = 'a'
        

if __name__ == '__main__':

    print("ok")



