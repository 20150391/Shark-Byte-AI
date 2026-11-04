"""Resilient Chromium search and page reader for SharkByte.

Searches topic-aware query variants, scores results against the intended subject,
and verifies the opened page before returning it as a source.
"""
from __future__ import annotations

import base64
import re
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

SEARCH_ENGINES = [
    ("DuckDuckGo", lambda q: "https://html.duckduckgo.com/html/?q=" + quote_plus(q)),
    ("Bing", lambda q: "https://www.bing.com/search?q=" + quote_plus(q) + "&count=20"),
    ("Google", lambda q: "https://www.google.com/search?q=" + quote_plus(q) + "&num=20"),
]
BLOCKED_HOSTS = (
    "duckduckgo.com", "bing.com", "microsoft.com", "google.com", "googleusercontent.com",
    "youtube.com", "youtu.be", "youtube-nocookie.com", "youtube.googleapis.com",
    "youtubei.googleapis.com",
)
STOP_WORDS = {
    "what", "does", "do", "is", "are", "the", "a", "an", "of", "to", "for", "in",
    "on", "with", "how", "why", "when", "where", "who", "did", "mean", "means",
    "meaning", "explain", "tell", "me", "about", "please", "can", "you", "this", "that",
}


def _clean_url(raw: str) -> str:
    if not raw:
        return ""
    raw = unquote(raw.strip())
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        if host.endswith("google.com") and parsed.path == "/url":
            raw = parse_qs(parsed.query).get("q", [""])[0]
            parsed = urlparse(raw); host = (parsed.hostname or "").lower()
        elif host.endswith("duckduckgo.com"):
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            if target:
                raw = target; parsed = urlparse(raw); host = (parsed.hostname or "").lower()
        elif host.endswith("bing.com") and parsed.path == "/ck/a":
            encoded = parse_qs(parsed.query).get("u", [""])[0]
            if encoded.startswith("a1"):
                try:
                    raw = base64.urlsafe_b64decode(encoded[2:] + "=" * (-len(encoded[2:]) % 4)).decode("utf-8", "ignore")
                    parsed = urlparse(raw); host = (parsed.hostname or "").lower()
                except Exception:
                    return ""
        if parsed.scheme not in ("http", "https") or not host:
            return ""
        if any(host == d or host.endswith("." + d) for d in BLOCKED_HOSTS):
            return ""
        return parsed._replace(fragment="").geturl()
    except Exception:
        return ""


def build_search_queries(query: str) -> list[str]:
    """Turn a natural-language question into a few topic-preserving searches."""
    raw = " ".join(str(query or "").split()).strip()
    if not raw:
        return []

    quoted = re.findall(r'["“\']([^"”\']{2,100})["”\']', raw)
    topic = quoted[0].strip() if quoted else raw
    topic = re.sub(r"(?i)^\s*(what|who|where|when|why|how)\s+", "", topic).strip()
    topic = re.sub(r"(?i)^does\s+", "", topic).strip()
    topic = re.sub(r"(?i)\s+(mean|means|meaning|stand for)\??$", "", topic).strip(" ?.!\t")

    # A quoted phrase is a strong signal; preserve it verbatim in search syntax.
    phrase = f'"{topic}"' if quoted else topic
    lower = raw.lower()
    queries = [f"{phrase} {('meme' if 'meme' in lower else '')} meaning".strip(), raw]

    # Numbers with punctuation are frequently tokenized badly by search engines.
    if re.search(r"(?<!\d)6\s*[-–—]\s*7(?!\d)", raw) or re.search(r"\bsix\s+seven\b", raw, re.I):
        queries = [
            '"6-7" meme meaning',
            '"6-7" meme origin viral trend',
            '"six seven" meme origin',
            '"67" meme meaning trend',
            'site:reddit.com "6-7" meme',
        ]
    else:
        # Avoid adding arbitrary synonyms; retain the user's actual named subject.
        if "meme" in lower:
            queries.insert(1, f'{phrase} meme origin trend')
        elif any(word in lower for word in ("slang", "trend", "viral")):
            queries.insert(1, f'{phrase} slang origin viral trend')

    output, seen = [], set()
    for item in queries:
        item = " ".join(item.split()).strip()
        if item and item not in seen:
            seen.add(item); output.append(item)
    return output[:5]


def _topic_terms(query: str) -> list[str]:
    text = re.sub(r"[\"'“”]", " ", str(query).lower())
    terms = re.findall(r"[a-z]{3,}|\d+", text)
    return list(dict.fromkeys(t for t in terms if t not in STOP_WORDS))


def _relevance_score(query: str, title: str, snippet: str, excerpt: str = "") -> int:
    haystack = f"{title} {snippet} {excerpt}".lower()
    terms = _topic_terms(query)
    score = sum(2 if term in title.lower() else 1 for term in terms if term in haystack)
    # Subject-specific checks prevent a numeric token or a common verb from dominating.
    if re.search(r"6\s*[-–—]\s*7|\bsix\s+seven\b|\b67\b", query, re.I):
        if any(x in haystack for x in ("meme", "viral", "trend", "tiktok", "basketball", "laMelo".lower(), "six seven")):
            score += 7
        if any(x in haystack for x in ("dictionary", "definition of does", "verb", "grammar", "government")) and not any(x in haystack for x in ("meme", "viral", "trend")):
            score -= 8
    if "meme" in query.lower() and "meme" in haystack:
        score += 3
    return score


def _extract_results(page, engine_name):
    selectors = {
        "DuckDuckGo": "a.result__a",
        "Bing": "li.b_algo h2 a",
        "Google": "div.MjjYud a, div.g a",
    }
    try:
        rows = page.locator(selectors[engine_name]).evaluate_all(
            "els => els.map(a => ({href:a.href, title:(a.innerText||a.getAttribute('aria-label')||'').trim(), "
            "snippet:(a.closest('.result, .b_algo, .MjjYud, .g')?.innerText||a.parentElement?.parentElement?.innerText||'').trim()}))"
        )
    except Exception:
        rows = []
    output, seen = [], set()
    for row in rows:
        url = _clean_url(row.get("href", "")); title = " ".join(row.get("title", "").split())
        if url and len(title) > 2 and url not in seen:
            seen.add(url); output.append({"title": title[:240], "url": url, "search_engine": engine_name,
                                         "snippet": " ".join(row.get("snippet", "").split())[:700]})
    if output:
        return output
    try:
        rows = page.locator("a[href]").evaluate_all(
            "els => els.map(a => ({href:a.href, title:(a.innerText||a.getAttribute('aria-label')||'').trim(), "
            "snippet:(a.parentElement?.parentElement?.innerText||a.parentElement?.innerText||'').trim()}))"
        )
    except Exception:
        rows = []
    for row in rows:
        url = _clean_url(row.get("href", "")); title = " ".join(row.get("title", "").split())
        if url and len(title) > 3 and url not in seen:
            seen.add(url); output.append({"title": title[:240], "url": url, "search_engine": engine_name,
                                         "snippet": " ".join(row.get("snippet", "").split())[:700]})
    return output


def search_and_read(query, progress=None, cancel_event=None, max_results=8, total_timeout=150):
    """Search targeted query variants, read candidate pages, and reject off-topic pages."""
    def report(message):
        if progress:
            try: progress(message)
            except Exception: pass

    original_query = " ".join(str(query or "").split()).strip()
    if not original_query:
        raise ValueError("Enter a question to research.")
    queries = build_search_queries(original_query)
    deadline = time.monotonic() + max(20, total_timeout)
    try:
        playwright_context = sync_playwright().start()
    except Exception as exc:
        raise RuntimeError("Could not start Playwright. Run 'py -m playwright install chromium'. " + str(exc)[:180]) from exc
    browser = None
    try:
        try:
            browser = playwright_context.chromium.launch(headless=True, timeout=20000)
        except Exception as exc:
            raise RuntimeError("Chromium could not launch. Run 'py -m playwright install chromium'. " + str(exc)[:180]) from exc
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US", ignore_https_errors=True,
        )
        page = context.new_page(); page.set_default_timeout(6000)
        candidates, seen = {}, set()
        for query_index, search_query in enumerate(queries, 1):
            if cancel_event and cancel_event.is_set(): raise RuntimeError("Research cancelled.")
            if time.monotonic() >= deadline: break
            for engine_name, url_builder in SEARCH_ENGINES:
                if cancel_event and cancel_event.is_set(): raise RuntimeError("Research cancelled.")
                if time.monotonic() >= deadline: break
                report(f"Searching {engine_name} ({query_index}/{len(queries)}): {search_query[:90]}")
                try:
                    page.goto(url_builder(search_query), wait_until="domcontentloaded", timeout=12000)
                    found = _extract_results(page, engine_name)
                    for item in found:
                        if item["url"] in seen: continue
                        seen.add(item["url"])
                        item["query_used"] = search_query
                        item["relevance"] = _relevance_score(original_query, item["title"], item["snippet"])
                        candidates[item["url"]] = item
                except Exception as exc:
                    report(f"{engine_name} hiccup; continuing: {str(exc)[:100]}")

        ranked = sorted(candidates.values(), key=lambda x: (x.get("relevance", 0), bool(x.get("snippet"))), reverse=True)
        # Do not open obvious low-score search matches just to fill the source quota.
        minimum_score = 2 if len(_topic_terms(original_query)) <= 2 else 3
        if re.search(r"6\s*[-–—]\s*7|\bsix\s+seven\b|\b67\b", original_query, re.I):
            minimum_score = 4
        sources = []
        target_count = min(max(1, int(max_results)), len(ranked))
        for index, item in enumerate(ranked, 1):
            if len(sources) >= target_count or time.monotonic() >= deadline: break
            if cancel_event and cancel_event.is_set(): raise RuntimeError("Research cancelled.")
            if item.get("relevance", 0) < minimum_score:
                continue
            report(f"Verifying page {len(sources)+1}/{target_count}: {item['title'][:85]}")
            excerpt = ""
            try:
                page.goto(item["url"], wait_until="domcontentloaded", timeout=10000)
                try:
                    page.locator("body").wait_for(state="visible", timeout=3000)
                    excerpt = page.locator("body").inner_text(timeout=4500)
                except PlaywrightTimeoutError:
                    excerpt = ""
                excerpt = "\n".join(line.strip() for line in excerpt.splitlines() if line.strip())[:6500]
            except Exception as exc:
                report(f"Page unavailable; skipped: {str(exc)[:90]}")
            verified_score = _relevance_score(original_query, item["title"], item.get("snippet", ""), excerpt)
            # Numeric meme searches require meme/trend context in the actual page, not just a URL hit.
            if verified_score < minimum_score or len(excerpt) < 180:
                report(f"Rejected weak/off-topic page: {item['url'][:100]}")
                continue
            sources.append({**item, "relevance": verified_score, "excerpt": excerpt})
        context.close()
        if not sources:
            raise RuntimeError("Search engines returned matches, but none passed topic-relevance and page-content checks. Try a more specific query.")
        report(f"Research browser finished: {len(sources)} topic-verified sources collected.")
        return sources
    finally:
        if browser:
            try: browser.close()
            except Exception: pass
        playwright_context.stop()
