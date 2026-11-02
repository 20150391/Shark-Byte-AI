"""Resilient headless Chromium search and page reader for SharkByte.

Uses Playwright to drive a real Chromium browser. Search engines and individual
pages are isolated so one blocked page cannot stall the whole research job.
"""
from __future__ import annotations

import base64
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


def search_and_read(query, progress=None, cancel_event=None, max_results=8, total_timeout=150):
    """Search several engines and read a bounded number of result pages.

    Every navigation has a short timeout and the complete operation has a hard
    deadline. Errors on one engine/page are logged and skipped, not fatal.
    """
    def report(message):
        if progress:
            try:
                progress(message)
            except Exception:
                pass

    deadline = time.monotonic() + max(20, total_timeout)
    results, seen = [], set()
    try:
        playwright_context = sync_playwright().start()
    except Exception as exc:
        raise RuntimeError(
            "Could not start Playwright. Run 'py -m playwright install chromium' from the SharkByte folder. "
            f"Details: {str(exc)[:180]}"
        ) from exc
    browser = None
    try:
        try:
            browser = playwright_context.chromium.launch(headless=True, timeout=20000)
        except Exception as exc:
            raise RuntimeError(
                "Chromium could not launch. Close SharkByte and run 'py -m playwright install chromium'. "
                f"Details: {str(exc)[:180]}"
            ) from exc
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US", ignore_https_errors=True,
        )
        page = context.new_page()
        page.set_default_timeout(7000)
        for engine_name, url_builder in SEARCH_ENGINES:
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("Research cancelled.")
            if time.monotonic() >= deadline:
                break
            report(f"Searching with {engine_name} in hidden Chromium…")
            try:
                page.goto(url_builder(query), wait_until="domcontentloaded", timeout=16000)
                try:
                    page.wait_for_load_state("networkidle", timeout=2500)
                except PlaywrightTimeoutError:
                    pass
                found = _extract_results(page, engine_name)
                for item in found:
                    if item["url"] not in seen:
                        seen.add(item["url"])
                        results.append(item)
                report(f"{engine_name}: found {len(found)} usable links.")
                if len(results) >= 4:
                    break
            except Exception as exc:
                report(f"{engine_name} search hiccup; moving to next engine: {str(exc)[:110]}")
        if not results:
            raise RuntimeError(
                "No search results could be extracted from DuckDuckGo, Bing, or Google. "
                "The connection may be blocked, a CAPTCHA may be present, or Chromium may not have internet access."
            )

        sources = []
        target_count = min(max(1, int(max_results)), len(results))
        for index, item in enumerate(results[:target_count], 1):
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("Research cancelled.")
            if time.monotonic() >= deadline:
                report("Research time limit reached; keeping pages already read.")
                break
            report(f"Reading page {index}/{target_count}: {item['title'][:90]}")
            excerpt = ""
            try:
                page.goto(item["url"], wait_until="domcontentloaded", timeout=12000)
                try:
                    page.locator("body").wait_for(state="visible", timeout=4000)
                    excerpt = page.locator("body").inner_text(timeout=5000)
                except PlaywrightTimeoutError:
                    excerpt = ""
                excerpt = "\n".join(line.strip() for line in excerpt.splitlines() if line.strip())[:6500]
                if len(excerpt) < 180:
                    report(f"Page had little readable text: {item['url'][:100]}")
            except Exception as exc:
                report(f"Page unavailable; skipped: {str(exc)[:100]}")
            if excerpt or item.get("snippet"):
                sources.append({**item, "excerpt": excerpt or "[Page text unavailable; search-result snippet only]"})
        context.close()
        if not sources:
            raise RuntimeError("Search results were found, but none of the result pages or snippets contained readable text.")
        report(f"Research browser finished: {len(sources)} usable sources collected.")
        return sources
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        playwright_context.stop()
