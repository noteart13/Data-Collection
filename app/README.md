# Suburb Property Scraping
This module contains steps to scrape listing URLs and property details.

## Step 1: Discover Property URLs
Navigate to the application directory and run:
```bash
cd app
python scraping_all_suburbs_step1.py
```

## Step 2: Scrape Detailed Property Listings
Once property URLs are generated, execute the batch crawler:
```bash
cd app
python scraping_all_suburbs.py
```