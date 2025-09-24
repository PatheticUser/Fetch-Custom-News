import requests
import feedparser
from fastapi import FastAPI, Query
from datetime import datetime
from typing import Optional, List, Dict
import re
import spacy
from geopy.geocoders import Nominatim
import time

app = FastAPI()

# -------------------------------
# RSS Feed Sources
# -------------------------------
RSS_FEEDS = {
    "BBC News": "http://feeds.bbci.co.uk/news/rss.xml",
    "Al Jazeera English": "https://www.aljazeera.com/xml/rss/all.xml",
    "Dawn": "https://www.dawn.com/feeds/home",
    "The News International": "https://www.thenews.com.pk/rss/1/1",
    "Geo News": "https://www.geo.tv/rss/1/1",
}

# -------------------------------
# Static Rank (as fallback)
# -------------------------------
SOURCE_RANK = {
    "BBC News": 95,
    "Al Jazeera English": 90,
    "Dawn": 85,
    "The News International": 80,
    "Geo News": 75,
}

# -------------------------------
# NLP + Geocoding Setup
# -------------------------------
nlp = spacy.load("en_core_web_sm")
geolocator = Nominatim(user_agent="news_app", timeout=30)
geo_cache = {}


def extract_locations(text: str):
    """Extract location names using spaCy NER"""
    doc = nlp(text)
    return [ent.text for ent in doc.ents if ent.label_ == "GPE"]


def geocode_location(place: str):
    """Convert place name to lat/lon with cache"""
    if place in geo_cache:
        return geo_cache[place]
    try:
        location = geolocator.geocode(place)
        time.sleep(1)  # avoid API rate limit
        if location:
            geo_cache[place] = {
                "lat": location.latitude,
                "lon": location.longitude,
                "name": place,
            }
            return geo_cache[place]
    except Exception as e:
        print(f"Geocoding error for {place}: {e}")
    return None


def enrich_article(article: Dict) -> Dict:
    """Add places (lat/lon) to article"""
    text = article["title"] + " " + (article.get("description") or "")
    places = extract_locations(text)
    coords = []
    for place in places:
        geo = geocode_location(place)
        if geo:
            coords.append(geo)
    article["places"] = coords
    return article


# -------------------------------
# Ranking
# -------------------------------
def get_dynamic_rank(source_name: str) -> int:
    base_rank = SOURCE_RANK.get(source_name, 50)
    popularity_factor = {
        "BBC News": 95,
        "Al Jazeera English": 88,
        "Dawn": 70,
        "The News International": 65,
        "Geo News": 60,
    }.get(source_name, 50)
    credibility_factor = {
        "BBC News": 97,
        "Al Jazeera English": 90,
        "Dawn": 80,
        "The News International": 75,
        "Geo News": 70,
    }.get(source_name, 50)
    final_rank = int((base_rank + popularity_factor + credibility_factor) / 3)
    return max(0, min(100, final_rank))


# -------------------------------
# RSS Parsing
# -------------------------------
def parse_rss_feed(feed_url: str, source_name: str) -> List[Dict]:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(feed_url, headers=headers, timeout=10)
        response.raise_for_status()
        feed = feedparser.parse(response.content)

        articles = []
        today = datetime.utcnow().date()

        for entry in feed.entries[:20]:
            published_date = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published_date = datetime(*entry.published_parsed[:6])
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published_date = datetime(*entry.updated_parsed[:6])
            else:
                published_date = datetime.utcnow()

            if published_date.date() == today:
                description = ""
                if hasattr(entry, "summary"):
                    description = re.sub("<[^<]+?>", "", entry.summary)
                elif hasattr(entry, "description"):
                    description = re.sub("<[^<]+?>", "", entry.description)

                articles.append(
                    {
                        "title": entry.title if hasattr(entry, "title") else "",
                        "description": description,
                        "url": entry.link if hasattr(entry, "link") else "",
                        "publishedAt": published_date.isoformat() + "Z",
                        "source": source_name,
                    }
                )
        return articles
    except Exception as e:
        print(f"Error parsing RSS feed for {source_name}: {e}")
        return []


# -------------------------------
# Fetch All News
# -------------------------------
def fetch_news(location: Optional[str] = None) -> List[Dict]:
    all_articles = []
    for source_name, feed_url in RSS_FEEDS.items():
        articles = parse_rss_feed(feed_url, source_name)
        all_articles.extend(articles)

    results = []
    for article in all_articles:
        # Add location enrichment FIRST so places are available
        article = enrich_article(article)

        source_name = article["source"]
        rank = get_dynamic_rank(source_name)

        # Region type (basic check)
        region_type = "other"
        if location:
            location_lower = location.lower()
            text = (article["title"] + " " + article.get("description", "")).lower()
            # Check for location in article's text
            if location_lower in text:
                region_type = "within_city"
            # Check for location in source name
            elif location_lower in source_name.lower():
                region_type = "within_region"

        article["rank"] = rank
        article["region_type"] = region_type
        results.append(article)

    # Sort the results by rank (descending) and then published date (ascending)
    results.sort(key=lambda x: (-x["rank"], x["publishedAt"]), reverse=False)
    return results


# -------------------------------
# API Endpoints
# -------------------------------
@app.get("/news/all")
def get_all_news(location: Optional[str] = Query(None)):
    return {"news": fetch_news(location)}


@app.get("/news/critical")
def get_critical_news(location: Optional[str] = Query(None)):
    news = fetch_news(location)
    return {"news": [n for n in news if n["rank"] >= 80]}


@app.get("/news/most_critical")
def get_most_critical_news(location: Optional[str] = Query(None)):
    news = fetch_news(location)
    return {"news": [n for n in news if n["rank"] >= 90]}


@app.get("/")
def root():
    return {
        "message": "News RSS Feed API with AI-powered Location Extraction",
        "endpoints": {
            "/news/all": "Get all news articles",
            "/news/critical": "Get critical news (rank >= 80)",
            "/news/most_critical": "Get most critical news (rank >= 90)",
        },
        "sources": list(RSS_FEEDS.keys()),
    }
