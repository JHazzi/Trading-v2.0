from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ADAPTER_VERSION = "alphavantage_news_evidence_v001"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return prefix + "-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _parse_av_time(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    # Alpha Vantage examples commonly use YYYYMMDDTHHMMSS.
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass
    return None


def canonical_url(url: Any) -> str | None:
    if not url:
        return None
    text = str(url).strip()
    try:
        p = urllib.parse.urlsplit(text)
    except Exception:
        return text
    # Remove common tracking params but preserve article identity params.
    drop = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","gclid","fbclid"}
    query = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    query = [(k,v) for k,v in query if k.lower() not in drop]
    return urllib.parse.urlunsplit((
        p.scheme.lower(), p.netloc.lower(), p.path,
        urllib.parse.urlencode(query), ""
    ))


def document_hash(title: str, url: str | None, published_at: str | None) -> str:
    payload = {
        "title": re.sub(r"\s+", " ", title.strip()).casefold(),
        "canonical_url": url or "",
        "published_at": published_at or "",
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def normalize_article(article: dict[str, Any], source_observation_id: str, retrieved_at: str) -> dict[str, Any]:
    title = str(article.get("title") or "").strip()
    if not title:
        raise ValueError("news article missing title")
    url = canonical_url(article.get("url"))
    published_at = _parse_av_time(article.get("time_published"))
    # Strict prospective policy: the system did not possess the article before
    # retrieval, even if provider publication time is earlier.
    available_at = retrieved_at
    doc_sha = document_hash(title, url, published_at)
    provider_doc_id = str(article.get("uuid") or article.get("id") or "").strip() or None

    doc_payload = {
        "source_observation_id": source_observation_id,
        "provider_document_id": provider_doc_id,
        "canonical_url": url,
        "title": title,
        "published_at": published_at,
        "document_sha256": doc_sha,
    }
    doc_id = _stable_id("news-document", doc_payload)

    assets = []
    for item in article.get("ticker_sentiment") or []:
        ticker = str(item.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        payload = {"news_document_id": doc_id, "ticker": ticker}
        assets.append({
            "observation_id": _stable_id("news-asset-annotation", payload),
            "news_document_id": doc_id,
            "asset_ticker": ticker,
            "provider_relevance_score": _float(item.get("relevance_score")),
            "provider_sentiment_score": _float(item.get("ticker_sentiment_score")),
            "provider_sentiment_label": _str_or_none(item.get("ticker_sentiment_label")),
            "available_at": available_at,
            "strict_pit": 1,
            "metadata_json": {"adapter_version": ADAPTER_VERSION, "provider_annotation_only": True},
        })

    topics = []
    for item in article.get("topics") or []:
        topic = str(item.get("topic") or "").strip()
        if not topic:
            continue
        payload = {"news_document_id": doc_id, "topic": topic}
        topics.append({
            "observation_id": _stable_id("news-topic-annotation", payload),
            "news_document_id": doc_id,
            "topic_key": topic,
            "provider_relevance_score": _float(item.get("relevance_score")),
            "available_at": available_at,
            "metadata_json": {"adapter_version": ADAPTER_VERSION, "provider_annotation_only": True},
        })

    return {
        "document": {
            "observation_id": doc_id,
            "source_observation_id": source_observation_id,
            "provider_document_id": provider_doc_id,
            "canonical_url": url,
            "title": title,
            "summary_text": _str_or_none(article.get("summary")),
            "publisher_name": _str_or_none(article.get("source")),
            "publisher_domain": _str_or_none(article.get("source_domain")),
            "language": None,
            "published_at": published_at,
            "first_seen_at": retrieved_at,
            "available_at": available_at,
            "strict_pit": 1,
            "document_sha256": doc_sha,
            "metadata_json": {
                "adapter_version": ADAPTER_VERSION,
                "authors": article.get("authors") or [],
                "category_within_source": article.get("category_within_source"),
                "overall_sentiment_score": _float(article.get("overall_sentiment_score")),
                "overall_sentiment_label": _str_or_none(article.get("overall_sentiment_label")),
                "provider_sentiment_is_annotation_not_truth": True,
            },
        },
        "assets": assets,
        "topics": topics,
    }


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _str_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def fetch_news_sentiment(
    api_key: str,
    *,
    tickers: str | None = None,
    topics: str | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
    limit: int = 1000,
    sort: str = "LATEST",
    timeout: int = 45,
) -> tuple[bytes, dict[str, Any], str]:
    params = {
        "function": "NEWS_SENTIMENT",
        "apikey": api_key,
        "limit": str(limit),
        "sort": sort,
    }
    if tickers:
        params["tickers"] = tickers
    if topics:
        params["topics"] = topics
    if time_from:
        params["time_from"] = time_from
    if time_to:
        params["time_to"] = time_to
    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode(params)
    retrieved_at = _now()
    req = urllib.request.Request(url, headers={"User-Agent": "quant-market-ai-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    payload = json.loads(raw.decode("utf-8"))
    if "Information" in payload or "Note" in payload:
        raise RuntimeError(payload.get("Information") or payload.get("Note"))
    if "Error Message" in payload:
        raise RuntimeError(payload["Error Message"])
    return raw, payload, retrieved_at


def api_key_from_env() -> str:
    key = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ALPHAVANTAGE_API_KEY is not set")
    return key
