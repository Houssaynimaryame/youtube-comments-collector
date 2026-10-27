"""Collecte de commentaires YouTube via la YouTube Data API v3.

Étapes couvertes (exercice du chapitre 2) :
  - authentification par clé API (lue depuis config/.env)
  - requêtes HTTP + gestion des erreurs / rate limit (get_with_retry)
  - pagination (nextPageToken)
  - extraction du texte et des métadonnées
"""
import hashlib
import logging
import os
import time

import requests
from dotenv import load_dotenv

log = logging.getLogger("collector")

BASE_URL = "https://www.googleapis.com/youtube/v3"

# Coût en unités de quota (voir slide 66 du cours)
COST_SEARCH = 100
COST_LIST = 1


class APIError(Exception):
    """Erreur définitive de l'API (pas de nouvelle tentative utile)."""


class QuotaExceeded(Exception):
    """Quota journalier épuisé : il faut attendre le lendemain."""


class CommentsDisabled(Exception):
    """Les commentaires sont désactivés sur cette vidéo."""


def new_stats():
    return {
        "requests": 0,
        "retries": 0,
        "failed_requests": 0,
        "quota_units": 0,
        "videos_comments_disabled": 0,
        "videos_failed": 0,
        "errors": [],
    }


def load_api_key(env_path):
    load_dotenv(env_path)
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key or key.startswith("COLLER"):
        raise RuntimeError(
            "Clé API absente : renseigner YOUTUBE_API_KEY dans config/.env"
        )
    return key


def _scrub(text, api_key):
    """Masque la clé API dans les messages d'erreur / logs."""
    return str(text).replace(api_key, "***")


def _error_reason(response):
    try:
        return response.json()["error"]["errors"][0]["reason"]
    except Exception:
        return ""


def get_with_retry(endpoint, params, api_key, stats, cost=COST_LIST, max_retries=5):
    """GET avec gestion des erreurs et des limites d'utilisation.

    - 200            : renvoie le JSON
    - 429 / 5xx      : attend (Retry-After si fourni, sinon backoff exponentiel) puis réessaie
    - erreur réseau  : même stratégie
    - quota épuisé   : lève QuotaExceeded
    - commentaires désactivés : lève CommentsDisabled
    - autres 4xx     : lève APIError
    """
    url = f"{BASE_URL}/{endpoint}"
    wait = 1

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                url, params={**params, "key": api_key}, timeout=30
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            stats["retries"] += 1
            log.warning(
                "Erreur réseau sur %s (tentative %d/%d) : %s",
                endpoint, attempt, max_retries, _scrub(exc, api_key),
            )
            time.sleep(wait)
            wait = min(wait * 2, 60)
            continue

        stats["requests"] += 1
        code = response.status_code

        if code == 200:
            stats["quota_units"] += cost
            return response.json()

        reason = _error_reason(response)

        if code == 403 and reason in ("quotaExceeded", "dailyLimitExceeded"):
            raise QuotaExceeded("Quota journalier de l'API épuisé.")
        if code == 403 and reason == "commentsDisabled":
            raise CommentsDisabled("Commentaires désactivés.")

        retriable = code == 429 or code >= 500 or reason == "rateLimitExceeded"
        if not retriable:
            stats["failed_requests"] += 1
            msg = f"HTTP {code} ({reason or 'sans détail'}) sur {endpoint}"
            stats["errors"].append(msg)
            raise APIError(msg)

        # Rate limit / erreur serveur : on attend puis on réessaie
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            wait = int(retry_after)
        stats["retries"] += 1
        log.warning(
            "HTTP %d sur %s : attente %ds (tentative %d/%d)",
            code, endpoint, wait, attempt, max_retries,
        )
        time.sleep(wait)
        wait = min(wait * 2, 60)

    stats["failed_requests"] += 1
    msg = f"Abandon après {max_retries} tentatives sur {endpoint}"
    stats["errors"].append(msg)
    raise APIError(msg)


def search_videos(api_key, query, stats, max_videos=5, published_after=None,
                  published_before=None, relevance_language=None):
    """Recherche de vidéos : renvoie une liste d'identifiants (videoId)."""
    video_ids, seen, token = [], set(), None

    while len(video_ids) < max_videos:
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": min(50, max_videos),
            "order": "relevance",
        }
        if published_after:
            params["publishedAfter"] = published_after
        if published_before:
            params["publishedBefore"] = published_before
        if relevance_language:
            params["relevanceLanguage"] = relevance_language
        if token:
            params["pageToken"] = token

        data = get_with_retry("search", params, api_key, stats, cost=COST_SEARCH)
        for item in data.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if vid and vid not in seen:
                seen.add(vid)
                video_ids.append(vid)

        token = data.get("nextPageToken")
        if not token:
            break

    return video_ids[:max_videos]


def get_video_details(api_key, video_ids, stats):
    """Informations détaillées (titre, date, statistiques) par lots de 50."""
    items = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        data = get_with_retry(
            "videos",
            {"part": "snippet,statistics", "id": ",".join(batch)},
            api_key, stats,
        )
        items.extend(data.get("items", []))
    return items


def get_comments(api_key, video_id, stats, max_comments=500, order="time"):
    """Commentaires de premier niveau d'une vidéo, avec pagination.

    order : "time" (les plus récents) ou "relevance" (les plus pertinents).
    """
    items, token = [], None

    while len(items) < max_comments:
        params = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": min(100, max_comments - len(items)),
            "order": order,
            "textFormat": "plainText",
        }
        if token:
            params["pageToken"] = token

        data = get_with_retry("commentThreads", params, api_key, stats)
        items.extend(data.get("items", []))

        token = data.get("nextPageToken")
        if not token:
            break
        time.sleep(0.1)  # politesse envers l'API

    return items[:max_comments]


def _pseudonymize(value):
    """Identifiant d'auteur pseudonymisé (hachage tronqué)."""
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def extract_comment(item, video_meta=None):
    """Transforme un commentaire brut de l'API en enregistrement plat."""
    top = item["snippet"]["topLevelComment"]
    sn = top["snippet"]
    author_channel = (sn.get("authorChannelId") or {}).get("value")
    video_id = sn.get("videoId") or item["snippet"].get("videoId")
    meta = (video_meta or {}).get(video_id, {})

    return {
        "comment_id": top.get("id"),
        "video_id": video_id,
        "video_title": meta.get("title"),
        "text": sn.get("textDisplay") or sn.get("textOriginal"),
        "author_hash": _pseudonymize(author_channel),
        "published_at": sn.get("publishedAt"),
        "updated_at": sn.get("updatedAt"),
        "like_count": sn.get("likeCount", 0),
        "reply_count": item["snippet"].get("totalReplyCount", 0),
        "content_type": "commentaire",
    }


def extract_video_meta(video_items):
    """Dictionnaire video_id -> métadonnées utiles."""
    out = {}
    for v in video_items:
        sn, st = v.get("snippet", {}), v.get("statistics", {})
        out[v["id"]] = {
            "title": sn.get("title"),
            "channel": sn.get("channelTitle"),
            "published_at": sn.get("publishedAt"),
            "view_count": st.get("viewCount"),
            "comment_count": st.get("commentCount"),
        }
    return out
