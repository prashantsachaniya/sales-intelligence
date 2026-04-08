import os
import re
import json
import time
import urllib.parse
import urllib.request
import urllib.error

import functions_framework
import requests
from bs4 import BeautifulSoup


HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Sales Intelligence Automator</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; }
    textarea { width: 100%; height: 220px; }
    button { padding: 10px 14px; margin-top: 10px; }
    .card { border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin-top: 20px; }
    .error { color: #b00020; }
    .muted { color: #666; }
  </style>
</head>
<body>
  <h1>Sales Intelligence Automator</h1>
  <div class="card">
    <form method="post">
      <p>Enter one lead per line:</p>
      <textarea name="leads">__LEADS_TEXT__</textarea><br>
      <button type="submit">Analyze</button>
    </form>
    <p class="muted">Now with website crawling for URL leads and official-site resolution for fuzzy leads.</p>
  </div>
  __RESULTS_HTML__
</body>
</html>
"""


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

MAX_INTERNAL_PAGES = int(os.getenv("MAX_INTERNAL_PAGES", "4"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))


def esc(s):
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_page(leads_text, results_html):
    return (
        HTML
        .replace("__LEADS_TEXT__", esc(leads_text))
        .replace("__RESULTS_HTML__", results_html)
    )


def is_url(text: str) -> bool:
    text = (text or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def dedupe_keep_order(items):
    out = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def same_domain(a: str, b: str) -> bool:
    try:
        da = urllib.parse.urlparse(a).netloc.replace("www.", "").lower()
        db = urllib.parse.urlparse(b).netloc.replace("www.", "").lower()
        return da == db
    except Exception:
        return False


def fetch_url(url: str) -> str:
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
        allow_redirects=True,
    )
    resp.raise_for_status()
    return resp.text


def clean_text_lines(lines):
    cleaned = []
    seen = set()

    bad_fragments = [
        "cookie", "privacy policy", "terms of service", "all rights reserved",
        "facebook", "instagram", "linkedin", "login", "sign up", "subscribe",
        "navigation", "menu"
    ]

    for line in lines:
        line = re.sub(r"\s+", " ", line).strip()
        if not line or len(line) < 25:
            continue

        low = line.lower()
        if any(x in low for x in bad_fragments):
            continue

        if low not in seen:
            seen.add(low)
            cleaned.append(line)

    return cleaned[:250]


def extract_page_text_and_links(base_url: str, html: str):
    soup = BeautifulSoup(html, "html.parser")

    links = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue

        full = urllib.parse.urljoin(base_url, href)
        if same_domain(base_url, full):
            links.append(full)

    meta_lines = []

    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(" ", strip=True):
        meta_lines.append(title_tag.get_text(" ", strip=True))

    for attr_name, attr_value in [
        ("name", "description"),
        ("property", "og:title"),
        ("property", "og:description"),
        ("name", "twitter:title"),
        ("name", "twitter:description"),
    ]:
        tag = soup.find("meta", attrs={attr_name: attr_value})
        if tag and tag.get("content"):
            meta_lines.append(tag.get("content", "").strip())

    json_ld_lines = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        txt = tag.string or tag.get_text(" ", strip=True)
        if txt:
            json_ld_lines.append(txt[:2000])

    for tag in soup(["script", "style", "noscript", "svg", "footer", "nav", "header", "aside"]):
        tag.decompose()

    lines = []
    for tag_name in ["h1", "h2", "h3", "h4", "p", "li", "span", "div"]:
        for tag in soup.find_all(tag_name):
            txt = tag.get_text(" ", strip=True)
            if txt:
                lines.append(txt)

    cleaned_lines = clean_text_lines(meta_lines + lines)

    if len("\n".join(cleaned_lines)) < 500:
        body_text = soup.get_text("\n", strip=True)
        broader_lines = [x.strip() for x in body_text.splitlines() if x.strip()]
        cleaned_lines = clean_text_lines(meta_lines + broader_lines)

    page_text = "\n".join(cleaned_lines[:300])

    if json_ld_lines:
        page_text += "\n\nSTRUCTURED_DATA:\n" + "\n".join(json_ld_lines[:3])

    return page_text[:12000], dedupe_keep_order(links)

def choose_priority_links(base_url: str, links):
    keywords = [
        "about", "service", "services", "product", "products", "solution", "solutions",
        "industry", "industries", "customer", "customers", "company", "who-we-are",
        "what-we-do", "contact"
    ]

    scored = []
    for link in links:
        low = link.lower()
        score = 0
        for kw in keywords:
            if kw in low:
                score += 1
        if score > 0:
            scored.append((score, link))

    scored.sort(key=lambda x: (-x[0], x[1]))
    ordered = [x[1] for x in scored]
    return dedupe_keep_order(ordered)[:MAX_INTERNAL_PAGES]


def resolve_official_website(query: str) -> str:
    search_url = "https://html.duckduckgo.com/html/"
    try:
        resp = requests.post(
            search_url,
            data={"q": query},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a.result__a"):
            href = (a.get("href") or "").strip()
            if href.startswith("http"):
                bad_domains = ["yelp.", "facebook.", "instagram.", "linkedin.", "youtube.", "mapquest."]
                if any(bad in href.lower() for bad in bad_domains):
                    continue
                return href
    except Exception:
        pass

    return ""


def normalize_lead(lead: str):
    lead = (lead or "").strip()
    if not lead:
        return {"type": "empty", "raw": lead}

    if is_url(lead):
        return {"type": "url", "raw": lead, "url": lead}

    parts = re.split(r"\s+[–-]\s+", lead, maxsplit=1)
    name = parts[0].strip()
    location = parts[1].strip() if len(parts) > 1 else ""
    return {"type": "search", "raw": lead, "name": name, "location": location}


def collect_lead_content(lead: str):
    meta = normalize_lead(lead)

    if meta["type"] == "empty":
        return {
            "lead_input": lead,
            "resolved_name": "",
            "resolved_url": "",
            "source_pages": [],
            "raw_content": "",
            "error": "Empty lead input",
        }

    if meta["type"] == "url":
        resolved_url = meta["url"]
        resolved_name = urllib.parse.urlparse(resolved_url).netloc.replace("www.", "")
    else:
        query = f"{meta.get('name', '')} {meta.get('location', '')} official website".strip()
        resolved_url = resolve_official_website(query)
        resolved_name = meta.get("name", "")

    if not resolved_url:
        return {
            "lead_input": lead,
            "resolved_name": meta.get("name", ""),
            "resolved_url": "",
            "source_pages": [],
            "raw_content": "",
            "error": "Could not resolve official website",
        }

    source_pages = []
    content_chunks = []

    try:
        homepage_html = fetch_url(resolved_url)
        homepage_text, homepage_links = extract_page_text_and_links(resolved_url, homepage_html)

        source_pages.append(resolved_url)
        content_chunks.append(f"PAGE: {resolved_url}\n{homepage_text}")

        priority_links = choose_priority_links(resolved_url, homepage_links)

        for page_url in priority_links:
            try:
                html = fetch_url(page_url)
                text, _ = extract_page_text_and_links(page_url, html)
                if text.strip():
                    source_pages.append(page_url)
                    content_chunks.append(f"PAGE: {page_url}\n{text}")
            except Exception:
                continue

        raw_content = "\n\n".join(content_chunks)[:30000]

        return {
            "lead_input": lead,
            "resolved_name": resolved_name,
            "resolved_url": resolved_url,
            "source_pages": source_pages,
            "raw_content": raw_content,
            "error": "",
        }

    except Exception as e:
        return {
            "lead_input": lead,
            "resolved_name": resolved_name,
            "resolved_url": resolved_url,
            "source_pages": source_pages,
            "raw_content": "",
            "error": f"Website crawl failed: {str(e)}",
        }


def gemini_request(model: str, prompt: str, api_key: str):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    candidates = body.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"No Gemini candidates returned for model {model}")

    parts = candidates[0].get("content", {}).get("parts", [])
    text = ""
    for part in parts:
        if "text" in part:
            text += part["text"]

    if not text.strip():
        raise RuntimeError(f"Gemini returned empty text for model {model}")

    return json.loads(text)


def call_gemini(prompt: str):
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    primary_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.0-flash").strip()

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    models_to_try = [primary_model]
    if fallback_model and fallback_model != primary_model:
        models_to_try.append(fallback_model)

    last_error = None

    for model in models_to_try:
        for attempt in range(3):
            try:
                return gemini_request(model, prompt, api_key)
            except urllib.error.HTTPError as e:
                try:
                    error_body = e.read().decode("utf-8")
                except Exception:
                    error_body = ""

                if e.code == 503:
                    last_error = f"Gemini 503 on model {model}, attempt {attempt + 1}: {error_body}"
                    time.sleep(2 * (attempt + 1))
                    continue

                raise RuntimeError(f"Gemini HTTP {e.code} on model {model}: {error_body}")
            except Exception as e:
                last_error = f"Gemini error on model {model}, attempt {attempt + 1}: {str(e)}"
                time.sleep(2 * (attempt + 1))

    raise RuntimeError(last_error or "Gemini request failed")


def analyze_lead(lead: str):
    collected = collect_lead_content(lead)

    if collected.get("error"):
        return {
            "lead_input": lead,
            "resolved_name": collected.get("resolved_name", ""),
            "resolved_url": collected.get("resolved_url", ""),
            "source_pages": collected.get("source_pages", []),
            "company_overview": "",
            "core_product_service": "",
            "target_customer": "",
            "b2b_qualified": "No",
            "qualification_reason": "",
            "sales_questions": [],
            "error": collected["error"],
        }
    prompt = f"""
    You are a strict sales research analyst working for Moksh Tech.

    Your task:
    Analyze the lead based only on the scraped website content and decide if this company appears to be a relevant B2B lead for Moksh Tech.

    Moksh Tech context:
    - Moksh Tech provides outsourced technology and business support services.
    - The sales team wants to identify businesses that could be relevant B2B prospects.
    - The 3 sales questions must be from Moksh Tech's perspective, not generic questions about the lead's own service.
    - The questions should help uncover business pain points around technology, operations, support, software, automation, process improvement, outsourcing, or scale.

    Return valid JSON only in this exact structure:
    {{
    "company_overview": "string",
    "core_product_service": "string",
    "target_customer": "string",
    "b2b_qualified": "Yes|No",
    "qualification_reason": "string",
    "sales_questions": ["q1", "q2", "q3"]
    }}

    Rules:
    - Use only the scraped website content below.
    - Do not invent facts.
    - Keep the summary concise but specific.
    - Return exactly 3 sales questions.
    - Questions must be useful for Moksh Tech's sales team.
    - A company can still be B2B Qualified = Yes even if it is not a tech company, as long as it appears to be a business that could reasonably need outsourced tech, support, software, process improvement, or operational help.
    - Mark B2B Qualified = No only if it clearly does not appear to be a meaningful business lead for Moksh Tech.

    Lead input: {collected["lead_input"]}
    Resolved company name: {collected["resolved_name"]}
    Resolved URL: {collected["resolved_url"]}

    Scraped content:
    {collected["raw_content"]}
    """

    try:
        result = call_gemini(prompt)
        questions = result.get("sales_questions", [])
        if not isinstance(questions, list):
            questions = []
        questions = [str(q).strip() for q in questions if str(q).strip()][:3]

        while len(questions) < 3:
            questions.append("What are your current business priorities this quarter?")

        return {
            "lead_input": lead,
            "resolved_name": collected["resolved_name"],
            "resolved_url": collected["resolved_url"],
            "source_pages": collected["source_pages"],
            "company_overview": str(result.get("company_overview", "")).strip(),
            "core_product_service": str(result.get("core_product_service", "")).strip(),
            "target_customer": str(result.get("target_customer", "")).strip(),
            "b2b_qualified": str(result.get("b2b_qualified", "No")).strip(),
            "qualification_reason": str(result.get("qualification_reason", "")).strip(),
            "sales_questions": questions,
            "error": ""
        }
    except Exception as e:
        return {
            "lead_input": lead,
            "resolved_name": collected["resolved_name"],
            "resolved_url": collected["resolved_url"],
            "source_pages": collected["source_pages"],
            "company_overview": "",
            "core_product_service": "",
            "target_customer": "",
            "b2b_qualified": "No",
            "qualification_reason": "",
            "sales_questions": [],
            "error": str(e)
        }


def render_results(results):
    if not results:
        return ""

    out = ['<div class="card"><h2>Results</h2>']
    for r in results:
        out.append(f"<h3>{esc(r['lead_input'])}</h3>")

        if r.get("resolved_url"):
            out.append(f"<p><strong>Resolved URL:</strong> {esc(r['resolved_url'])}</p>")

        if r["error"]:
            out.append(f"<p class='error'><strong>Error:</strong> {esc(r['error'])}</p>")
        else:
            out.append(f"<p><strong>Company Overview:</strong> {esc(r['company_overview'])}</p>")
            out.append(f"<p><strong>Core Product/Service:</strong> {esc(r['core_product_service'])}</p>")
            out.append(f"<p><strong>Target Customer:</strong> {esc(r['target_customer'])}</p>")
            out.append(f"<p><strong>B2B Qualified:</strong> {esc(r['b2b_qualified'])}</p>")
            out.append(f"<p><strong>Qualification Reason:</strong> {esc(r.get('qualification_reason', ''))}</p>")

            out.append("<p><strong>Three Sales Questions:</strong></p><ul>")
            for q in r["sales_questions"]:
                out.append(f"<li>{esc(q)}</li>")
            out.append("</ul>")

            if r.get("source_pages"):
                out.append("<p><strong>Source Pages Crawled:</strong></p><ul>")
                for page in r["source_pages"]:
                    safe = esc(page)
                    out.append(f"<li><a href='{safe}' target='_blank'>{safe}</a></li>")
                out.append("</ul>")

        out.append("<hr>")
    out.append("</div>")
    return "".join(out)


@functions_framework.http
def sales_app(request):
    if request.method == "GET":
        return render_page("", "")

    content_type = (request.headers.get("Content-Type") or "").lower()

    if "application/json" in content_type:
        payload = request.get_json(silent=True) or {}
        leads = payload.get("leads", [])
        if isinstance(leads, str):
            leads = [x.strip() for x in leads.splitlines() if x.strip()]
        results = [analyze_lead(x) for x in leads]
        return {"ok": True, "results": results}

    leads_text = request.form.get("leads", "")
    leads = [x.strip() for x in leads_text.splitlines() if x.strip()]
    results = [analyze_lead(x) for x in leads]

    return render_page(leads_text, render_results(results))