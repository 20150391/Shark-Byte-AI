"""Resilient headless Chromium search and page reader for SharkByte.

Uses Playwright to drive a real Chromium browser. Search engines and individual
pages are isolated so one blocked page cannot stall the whole research job.
"""
from __future__ import annotations

import base64
import threading
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

SEARCH_ENGINES = [
    ("DuckDuckGo", lambda q: "https://html.duckduckgo.com/html/?q=" + quote_plus(q)),
    ("Bing", lambda q: "https://www.bing.com/search?q=" + quote_plus(q) + "&count=20"),
    ("Google", lambda q: "https://www.google.com/search?q=" + quote_plus(q) + "&num=20"),
]
BLOCKED_HOSTS = ("duckduckgo.com", "bing.com", "microsoft.com", "google.com", "googleusercontent.com")


def _clean_url(raw: str) -> str:
    if not raw:
        return ""
    raw = unquote(raw.strip())
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        if host.endswith("google.com") and parsed.path == "/url":
            raw = parse_qs(parsed.query).get("q", [""])[0]
            parsed = urlparse(raw)
            host = (parsed.hostname or "").lower()
        elif host.endswith("duckduckgo.com"):
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            if target:
                raw = target
                parsed = urlparse(raw)
                host = (parsed.hostname or "").lower()
        elif host.endswith("bing.com") and parsed.path == "/ck/a":
            encoded = parse_qs(parsed.query).get("u", [""])[0]
            if encoded.startswith("a1"):
                try:
                    raw = base64.urlsafe_b64decode(encoded[2:] + "=" * (-len(encoded[2:]) % 4)).decode("utf-8", "ignore")
                    parsed = urlparse(raw)
                    host = (parsed.hostname or "").lower()
                except Exception:
                    return ""
        
        if parsed.scheme not in ("http", "https") or not host:
            return ""
        if any(host == d or host.endswith("." + d) for d in BLOCKED_HOSTS):
            return ""
        return parsed._replace(fragment="").geturl()
    except Exception:
        return ""


def _extract_results(page, engine_name):
    # Engine-specific selectors first; broad anchor fallback only if needed.
    selectors = {
        "DuckDuckGo": "a.result__a",
        "Bing": "li.b_algo h2 a",
        "Google": "div.MjjYud a, div.g a",
    }
    rows = page.locator(selectors[engine_name]).evaluate_all(
        "els => els.map(a => ({href:a.href, title:(a.innerText||a.getAttribute('aria-label')||'').trim(), "
        "snippet:(a.closest('.result, .b_algo, .MjjYud, .g')?.innerText||a.parentElement?.parentElement?.innerText||'').trim()}))"
    )
    output, seen = [], set()
    for row in rows:
        url = _clean_url(row.get("href", ""))
        title = " ".join(row.get("title", "").split())
        if url and len(title) > 2 and url not in seen:
            seen.add(url)
            output.append({"title": title[:240], "url": url,
                           "snippet": " ".join(row.get("snippet", "").split())[:700]})
    if output:
        return output
    # Some engine layouts change frequently; inspect all anchors as a fallback.
    rows = page.locator("a[href]").evaluate_all(
        "els => els.map(a => ({href:a.href, title:(a.innerText||a.getAttribute('aria-label')||'').trim(), "
        "snippet:(a.parentElement?.parentElement?.innerText||a.parentElement?.innerText||'').trim()}))"
    )
    for row in rows:
        url = _clean_url(row.get("href", ""))
        title = " ".join(row.get("title", "").split())
        if url and len(title) > 3 and url not in seen:
            seen.add(url)
            output.append({"title": title[:240], "url": url,
                           "snippet": " ".join(row.get("snippet", "").split())[:700]})
    return output


def extract_keywords(query):
    """Extract useful topic words regardless of punctuation or query format."""
    import re
    text = unquote(str(query or "")).lower()
    # URLs and punctuation become separators; preserve letters, digits and useful hyphenated terms.
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    tokens = re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)?", text)
    stop = {
        "a","an","the","and","or","but","if","then","else","when","where","what","why","how",
        "who","whom","which","is","are","was","were","be","been","being","do","does","did",
        "can","could","would","should","will","shall","may","might","must","to","of","in","on",
        "at","by","for","from","with","about","into","over","after","before","between","through",
        "this","that","these","those","it","its","i","me","my","we","our","you","your","they",
        "them","their","he","she","his","her","as","than","also","please","find","search","tell",
        "explain","give","show","look","up","latest","current"
    }
    useful = []
    for token in tokens:
        if token not in stop and (len(token) > 1 or token.isdigit()) and token not in useful:
            useful.append(token)
    return useful[:12] or tokens[:8]


def _rank_results(items, keywords):
    """Simple lexical ranking; intentionally no embeddings or vector database."""
    scored = []
    for item in items:
        title = str(item.get("title", "")).lower()
        snippet = str(item.get("snippet", "")).lower()
        url = str(item.get("url", "")).lower()
        score = 0
        for word in keywords:
            if word in title: score += 5
            if word in snippet: score += 2
            if word in url: score += 1
        item["relevance"] = score
        scored.append(item)
    return sorted(scored, key=lambda x: x.get("relevance", 0), reverse=True)


def _launch(playwright_context):
    try:
        return playwright_context.chromium.launch(headless=True, timeout=20000)
    except Exception as exc:
        raise RuntimeError(
            "Chromium could not launch. Close SharkByte and run 'py -m playwright install chromium'. "
            f"Details: {str(exc)[:180]}"
        ) from exc


def _start_playwright():
    try:
        return sync_playwright().start()
    except Exception as exc:
        raise RuntimeError(
            "Could not start Playwright. Run 'py -m playwright install chromium' from the SharkByte folder. "
            f"Details: {str(exc)[:180]}"
        ) from exc


def _new_context(browser):
    return browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        locale="en-US", ignore_https_errors=True,
    )


def _search_candidates(queries, progress=None, cancel_event=None, total_timeout=210):
    """Search every engine with every query and return ALL unique results (title/url/snippet only).

    No per-engine or total cap is applied: the AI decides which titles are worth opening.
    Pages are NOT opened here; use read_pages() for the ones the AI selected.
    """
    def report(message):
        if progress:
            try: progress(message)
            except Exception: pass

    if isinstance(queries, str):
        queries = [queries]
    queries = [" ".join(str(q or "").split()) for q in (queries or [])]
    queries = [q for q in dict.fromkeys(queries) if q]
    if not queries:
        raise RuntimeError("No search keywords were provided.")
    keywords = []
    for q in queries:
        for word in extract_keywords(q):
            if word not in keywords:
                keywords.append(word)

    deadline = time.monotonic() + max(20, total_timeout)
    results, seen = [], set()
    playwright_context = _start_playwright()
    browser = None
    try:
        browser = _launch(playwright_context)
        context = _new_context(browser)
        page = context.new_page()
        page.set_default_timeout(7000)
        for query in queries:
            for engine_name, url_builder in SEARCH_ENGINES:
                if cancel_event and cancel_event.is_set():
                    raise RuntimeError("Research cancelled.")
                if time.monotonic() >= deadline:
                    break
                report(f"Searching {engine_name} for: {query}")
                try:
                    page.goto(url_builder(query), wait_until="domcontentloaded", timeout=16000)
                    try: page.wait_for_load_state("networkidle", timeout=2000)
                    except PlaywrightTimeoutError: pass
                    added = 0
                    for item in _extract_results(page, engine_name):
                        if item["url"] not in seen:
                            seen.add(item["url"])
                            item["search_engine"] = engine_name
                            item["query"] = query
                            results.append(item)
                            added += 1
                    report(f"{engine_name}: collected {added} unique results.")
                except Exception as exc:
                    report(f"{engine_name} search hiccup; continuing: {str(exc)[:110]}")
        context.close()
    finally:
        if browser:
            try: browser.close()
            except Exception: pass
        playwright_context.stop()
    if not results:
        raise RuntimeError(
            "No search results could be extracted from DuckDuckGo, Bing, or Google. "
            "The connection may be blocked, a CAPTCHA may be present, or Chromium may not have internet access."
        )
    return _rank_results(results, keywords)


def _read_pages(items, progress=None, cancel_event=None, total_timeout=150, excerpt_chars=5200):
    """Open ONLY the supplied (AI-selected) results and return them with page text in item['excerpt']."""
    def report(message):
        if progress:
            try: progress(message)
            except Exception: pass

    items = list(items or [])
    if not items:
        return []
    deadline = time.monotonic() + max(20, total_timeout)
    playwright_context = _start_playwright()
    browser = None
    sources = []
    try:
        browser = _launch(playwright_context)
        context = _new_context(browser)
        page = context.new_page()
        page.set_default_timeout(7000)
        for index, item in enumerate(items, 1):
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("Research cancelled.")
            if time.monotonic() >= deadline:
                report("Browser time limit reached; keeping pages already read.")
                break
            report(f"Reading selected page {index}/{len(items)}: {item['title'][:90]}")
            excerpt = ""
            try:
                page.goto(item["url"], wait_until="domcontentloaded", timeout=10000)
                try:
                    page.locator("body").wait_for(state="visible", timeout=3500)
                    excerpt = page.locator("body").inner_text(timeout=4500)
                except PlaywrightTimeoutError:
                    excerpt = ""
                excerpt = "\n".join(line.strip() for line in excerpt.splitlines() if line.strip())[:excerpt_chars]
            except Exception as exc:
                report(f"Page unavailable; retaining snippet if useful: {str(exc)[:100]}")
            if excerpt or item.get("snippet"):
                sources.append({**item, "excerpt": excerpt or "[Page text unavailable; search-result snippet only]"})
        context.close()
    finally:
        if browser:
            try: browser.close()
            except Exception: pass
        playwright_context.stop()
    report(f"Browser finished: {len(sources)} selected pages read.")
    return sources


_BROWSER_LOCK = threading.Lock()


def _run_exclusive(fn, progress, cancel_event, *args, **kwargs):
    """Only one Playwright/Chromium session may run at a time in this process. Several mini-agents
    starting and stopping Playwright from different threads at once can deadlock (especially on Windows),
    so they take turns here instead."""
    waited = 0
    while not _BROWSER_LOCK.acquire(timeout=1):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Research cancelled.")
        waited += 1
        if waited == 3 and progress:
            try: progress("Waiting for another research browser session to finish…")
            except Exception: pass
        if waited > 900:
            raise RuntimeError("The research browser stayed busy for 15 minutes; giving up on this search.")
    try:
        return fn(*args, **kwargs)
    finally:
        _BROWSER_LOCK.release()


def search_candidates(queries, progress=None, cancel_event=None, total_timeout=210):
    return _run_exclusive(_search_candidates, progress, cancel_event,
                          queries, progress, cancel_event, total_timeout)


def read_pages(items, progress=None, cancel_event=None, total_timeout=150, excerpt_chars=5200):
    return _run_exclusive(_read_pages, progress, cancel_event,
                          items, progress, cancel_event, total_timeout, excerpt_chars)


def search_and_read(query, progress=None, cancel_event=None, max_results=15, total_timeout=210,
                    results_per_engine=10):
    """Legacy one-shot helper (still used by the file-download finder): search, rank, read the top N."""
    keywords = extract_keywords(query)
    search_query = " ".join(keywords) if keywords else " ".join(str(query or "").split())
    candidates = search_candidates([search_query], progress, cancel_event, total_timeout)
    shortlist = candidates[:max(1, min(15, int(max_results)))]
    sources = read_pages(shortlist, progress, cancel_event, total_timeout=max(20, total_timeout // 2))
    if not sources:
        raise RuntimeError("Search results were found, but none contained readable page text or snippets.")
    return sources
