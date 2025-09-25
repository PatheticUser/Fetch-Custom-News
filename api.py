import requests
import feedparser
from fastapi import FastAPI, Query
from datetime import datetime
from typing import Optional, List, Dict
import re
import spacy
from geopy.geocoders import Nominatim
import time
import math


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

# New York City coordinates for reference
NEW_YORK_COORDS = (40.7128, -74.0060)


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


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance between two points in kilometers using the Haversine formula"""
    R = 6371.0  # Earth radius in kilometers

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    distance_km = R * c
    return distance_km


def enrich_article(article: Dict) -> Dict:
    """Add places (lat/lon) and place names, plus distance from New York"""
    text = article["title"] + " " + (article.get("description") or "")
    places = extract_locations(text)
    coords = []
    for place in places:
        geo = geocode_location(place)
        if geo:
            # Calculate distance from New York
            distance = haversine_distance(
                NEW_YORK_COORDS[0], NEW_YORK_COORDS[1], geo["lat"], geo["lon"]
            )
            geo["Radius"] = distance
            coords.append(geo)
    article["places"] = coords  # List of dicts with lat, lon, name, distance_from_ny_km
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
    location_lower = location.lower() if location else None
    for article in all_articles:
        source_name = article["source"]
        rank = get_dynamic_rank(source_name)
        # Enrich article with extracted places and distances
        article = enrich_article(article)
        article["rank"] = rank
        results.append(article)
    # Sort so highest rank and newest articles come first
    results.sort(key=lambda x: (x["rank"], x["publishedAt"]), reverse=True)
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
