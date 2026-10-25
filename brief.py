"""
Morning Brief generator
=======================
Runs once a day (GitHub Actions). It:
  1. Pulls fresh headlines (with links and related coverage) from free
     Google News RSS feeds.
  2. Asks Claude to pick the stories that matter and write the brief in a
     fixed set of sections, citing the headlines it used.
  3. Saves the result as JSON under docs/data/ for the phone app to show.
  4. Optionally pings your phone through ntfy.

Cost: with web search off (the default) a run is roughly half a US cent on
Claude Haiku, i.e. well under $0.50 a month.

Environment variables
  ANTHROPIC_API_KEY  (required for a real run)
  CLAUDE_MODEL       default: claude-haiku-5-5
  FALLBACK_MODEL     default: claude-sonnet-5-5 (used only if the first model errors)
  SERPAPI_KEY        optional; adds Google News results with article snippets
  SERPAPI_PER_DAY    default: 5    (SerpApi credits used per run; ~150/month)
  MAX_SEARCHES       default: 0    (optional Claude web searches per run, $10 per 1,000;
                                    e.g. 3 adds about $0.90/month for fresher numbers)
  NTFY_TOPIC         optional; your private ntfy topic name for phone alerts
  APP_URL            optional; link opened when you tap the alert
  OUTPUT_DIR         default: docs/data
  KEEP_DAYS          default: 45   (how many past briefs to keep)

Run locally without an API key:  python brief.py --mock
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ─────────────────────────────────────────────────────────────
# SETTINGS — edit these to change what the brief covers
# ─────────────────────────────────────────────────────────────

TZ = ZoneInfo("Asia/Dubai")

# Kept deliberately general: this file lives in a public repo.
READER_PROFILE = """\
The reader works in business development for international private medical
insurance (IPMI), dealing with brokers worldwide. Based in Dubai, originally
from Kerala, India, and plans to move back to Kerala in the coming years.
Invests in global equity ETFs and follows Indian markets. Sends money home,
so the rupee-dirham rate matters. Cares about Indian politics (who governs
and how), the Andaman & Nicobar Islands (especially the Great Nicobar
project), and environmental issues. Wants short headlines for general news
and depth on the topics above."""

SECTIONS = [
    {
        "id": "spotlight",
        "title": "Spotlight",
        "brief": "The 3-4 biggest stories globally and in India right now. Keep these SHORT: "
                 "summary only, detail at most 2 sentences.",
        "items": "3-4",
        "feeds": ["TOP_IN", "WORLD"],
    },
    {
        "id": "india_politics",
        "title": "Indian politics",
        "brief": "Who is governing, what the government and opposition are doing, courts, "
                 "elections, Election Commission, major policy. Give both sides where contested.",
        "items": "2-4",
        "feeds": ["India politics", "Election Commission India", "Supreme Court India"],
        "serp": ["India politics BJP Congress"],
    },
    {
        "id": "andaman_environment",
        "title": "Andaman & Nicobar · Environment",
        "brief": "Any news on the Andaman & Nicobar Islands and the Great Nicobar project "
                 "(courts, clearances, tribal rights, forests). Then the most important "
                 "environmental stories in India and globally. If there is no new A&N news, "
                 "say so in one item rather than repeating old news.",
        "items": "2-4",
        "feeds": ["Great Nicobar", "Andaman Nicobar", "India environment", "climate"],
        "serp": ["Great Nicobar project"],
    },
    {
        "id": "insurance",
        "title": "Insurance & health",
        "brief": "International private medical insurance (IPMI), expat health insurance, "
                 "UAE/GCC health insurance regulation, big insurer or broker moves, "
                 "insurance fraud/compliance cases, medical cost trends. Skip if nothing real.",
        "items": "1-3",
        "feeds": ["international health insurance", "health insurance UAE", "IRDAI"],
        "serp": ["international private medical insurance", "UAE health insurance"],
    },
    {
        "id": "uae",
        "title": "UAE & Gulf",
        "brief": "Things that affect daily life in Dubai/UAE (rules, visas, fuel prices, "
                 "transport, events) and Gulf security that affects travel and flights.",
        "items": "2-3",
        "feeds": ["Dubai", "UAE", "Gulf flights"],
    },
    {
        "id": "markets",
        "title": "Money & markets",
        "brief": "Sensex/Nifty (last close and GIFT Nifty pre-open if available), what's "
                 "driving them, FII flows, crude oil, rupee vs dollar and dirham, gold, "
                 "and global markets / Fed / anything moving global equity ETFs. Context, "
                 "not trading tips.",
        "items": "2-4",
        "feeds": ["Sensex Nifty", "GIFT Nifty", "rupee dollar", "global markets"],
        "serp": ["Sensex Nifty today"],
    },
    {
        "id": "hidden_gems",
        "title": "Hidden gems",
        "brief": "3-4 genuinely interesting stories that are NOT in the headlines yet: new "
                 "science, quiet policy changes, emerging trends, surprising data, India "
                 "innovation. Avoid anything already covered above.",
        "items": "3-4",
        "feeds": ["SCIENCE", "TECHNOLOGY", "India startup", "research study"],
    },
]

GN_BASE = "https://news.google.com/rss"
GN_PARAMS = "hl=en-IN&gl=IN&ceid=IN:en"
GN_TOPICS = {
    "TOP_IN": f"{GN_BASE}?{GN_PARAMS}",
    "WORLD": f"{GN_BASE}/headlines/section/topic/WORLD?{GN_PARAMS}",
    "SCIENCE": f"{GN_BASE}/headlines/section/topic/SCIENCE?{GN_PARAMS}",
    "TECHNOLOGY": f"{GN_BASE}/headlines/section/topic/TECHNOLOGY?{GN_PARAMS}",
}

HEADLINES_PER_FEED = 10
API_URL = "https://api.anthropic.com/v1/messages"
WEB_SEARCH_TOOL = "web_search_20260209"


# ─────────────────────────────────────────────────────────────
# 1. Seed headlines from Google News RSS (free)
# ─────────────────────────────────────────────────────────────

def feed_url(name: str) -> str:
    if name in GN_TOPICS:
        return GN_TOPICS[name]
    q = urllib.parse.quote(f"{name} when:1d")
    return f"{GN_BASE}/search?q={q}&{GN_PARAMS}"


def fetch_feed(name: str) -> list[dict]:
    try:
        r = requests.get(feed_url(name), timeout=15,
                         headers={"User-Agent": "Mozilla/5.0 (MorningBrief)"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:  # one bad feed must not sink the run
        print(f"  ! feed '{name}' failed: {e}")
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        # Google appends " - Source" to titles; drop it
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        if title:
            out.append({"title": title, "source": source,
                        "link": (item.findtext("link") or "").strip(),
                        "published": (item.findtext("pubDate") or "").strip()[:16],
                        "related": related_coverage(item.findtext("description") or "", title)})
        if len(out) >= HEADLINES_PER_FEED:
            break
    return out


def related_coverage(desc_html: str, own_title: str) -> list[str]:
    """Google News descriptions list other outlets' headlines on the same story.
    These give Claude more angles to write from, at no cost."""
    pairs = re.findall(r"<a [^>]*>(.*?)</a>(?:&nbsp;|\s)*<font[^>]*>(.*?)</font>", desc_html, re.S)
    out = []
    for t, src in pairs:
        t = re.sub(r"<[^>]+>", "", t).strip()
        if t and t != own_title:
            out.append(f"{t} ({re.sub(r'<[^>]+>', '', src).strip()})")
    return out[:3]


def fetch_serpapi(query: str, api_key: str) -> list[dict]:
    """Google News results from the last 24h via SerpApi. Unlike the free feeds,
    these include a short snippet of each article. One call = one search credit."""
    try:
        r = requests.get("https://serpapi.com/search", timeout=20, params={
            "engine": "google", "tbm": "nws", "tbs": "qdr:d", "q": query,
            "gl": "in", "hl": "en", "num": 10, "api_key": api_key})
        data = r.json()
    except Exception as e:
        print(f"  ! SerpApi '{query}' failed: {e}")
        return []
    if data.get("error"):
        print(f"  ! SerpApi '{query}': {data['error']}")
        return []
    out = []
    for n in data.get("news_results", [])[:HEADLINES_PER_FEED]:
        src = n.get("source")
        if isinstance(src, dict):
            src = src.get("name", "")
        if n.get("title"):
            out.append({"title": n["title"].strip(), "source": (src or "").strip(),
                        "link": n.get("link", ""), "published": n.get("date", ""),
                        "snippet": (n.get("snippet") or "").strip()[:300], "related": []})
    return out


def gather_seeds() -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """Returns headlines per section, each with a short id like 'h12',
    plus a lookup from id to headline so cited ids can become links."""
    seeds, by_id, seen = {}, {}, set()
    serp_key = os.getenv("SERPAPI_KEY", "").strip()
    serp_budget = [int(os.getenv("SERPAPI_PER_DAY", "5") or 0)]
    for sec in SECTIONS:
        items, found = [], []
        # SerpApi first (has snippets), so its copy wins when both find a story
        for q in (sec.get("serp", []) if serp_key else []):
            if serp_budget[0] <= 0:
                break
            serp_budget[0] -= 1
            found += fetch_serpapi(q, serp_key)
        for f in sec["feeds"]:
            found += fetch_feed(f)
        for h in found:
            key = re.sub(r"\W+", "", h["title"].lower())[:70]
            if key in seen:
                continue
            seen.add(key)
            h["id"] = f"h{len(by_id) + 1}"
            by_id[h["id"]] = h
            items.append(h)
        seeds[sec["id"]] = items
        print(f"  {sec['id']}: {len(items)} headlines")
    return seeds, by_id


# ─────────────────────────────────────────────────────────────
# 2. Ask Claude to research and write the brief
# ─────────────────────────────────────────────────────────────

def build_prompt(today: str, seeds: dict, yesterday_headlines: list[str],
                 searches_enabled: bool = False) -> str:
    sec_lines = []
    for s in SECTIONS:
        sec_lines.append(f'- id "{s["id"]}" · title "{s["title"]}" · {s["items"]} items\n  {s["brief"]}')
    seed_lines = []
    for s in SECTIONS:
        seed_lines.append(f"## {s['id']}")
        for h in seeds.get(s["id"], []):
            line = f"[{h['id']}] {h['title']} — {h['source']}, {h['published']}"
            if h.get("snippet"):
                line += f"\n    snippet: {h['snippet']}"
            if h.get("related"):
                line += "\n    also: " + " | ".join(h["related"])
            seed_lines.append(line)
    yest = "\n".join(f"- {h}" for h in yesterday_headlines) or "(none)"
    search_note = (
        ("You may also use web search (sparingly) to confirm key numbers or fill an empty section. "
         "For facts found by search, also add \"sources\": [{\"name\": ..., \"url\": ...}] to the item.")
        if searches_enabled else
        "You do not have web search. Work only from these headlines."
    )

    return f"""You are writing a personal morning news brief for {today} (Dubai time).

# The reader
{READER_PROFILE}

# Sections, in this order
{chr(10).join(sec_lines)}

# Headlines from Google News, last ~24h
Each has an id in [brackets]; "snippet:" lines quote the start of the article;
"also:" lines are other outlets' headlines on the same
story, useful for extra facts and different angles. Headlines are grouped by the search
that found them; you may use any headline in any section where it fits best.
{search_note}
{chr(10).join(seed_lines)}

# Already covered in yesterday's brief
Do not repeat these unless something genuinely new happened; if so, say what's new.
{yest}

# Rules
- Current facts (events, numbers, quotes, who did what) must come from the headlines
  above. Never invent them. If a number isn't in the headlines, leave it out.
- You may add short, well-established background (what an institution is, how a policy
  works, what the long-running dispute is about) to explain why a story matters, but
  keep it general and don't present it as today's news.
- Plain, friendly English. No hype. Political topics: neutral, give each side's position
  where the headlines show it.
- summary: 1-2 sentences. detail: 2-4 sentences: what happened, the background, and
  what's next. Keep the whole brief to about 20 stories. why_it_matters: one sentence tied to the reader, or "" if nothing real.
- Every item lists the ids of the headlines it is based on in source_ids (1-3 ids).
- If a section has no real news in the headlines, return it with an empty items list.
  Never pad a section with weak stories.
- market_snapshot: up to 6 key numbers (e.g. Nifty, Sensex, GIFT Nifty, Brent, USD/INR,
  gold) ONLY if they appear in the headlines; direction is "up", "down" or "flat".

# Output
Output ONLY this JSON inside <brief></brief> tags. No text before or after it, no code fences:
<brief>
{{
  "overview": "one sentence on the shape of the day",
  "market_snapshot": [{{"label": "Nifty 50", "value": "22,600", "change": "-0.4%", "direction": "down"}}],
  "sections": [
    {{"id": "spotlight", "title": "Spotlight", "items": [
      {{"headline": "...", "summary": "...", "detail": "...", "why_it_matters": "",
        "source_ids": ["h3", "h17"]}}
    ]}}
  ]
}}
</brief>"""


def call_claude(prompt: str, model: str, api_key: str, max_searches: int) -> str:
    """Runs the request, continuing if the server pauses a long search turn.
    Returns all assistant text joined together."""
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    messages = [{"role": "user", "content": prompt}]
    texts: list[str] = []
    usage_total = {"input_tokens": 0, "output_tokens": 0, "web_search_requests": 0}

    for turn in range(6):
        body = {"model": model, "max_tokens": 16000, "messages": messages}
        if max_searches > 0:
            body["tools"] = [{"type": WEB_SEARCH_TOOL, "name": "web_search",
                              "max_uses": max_searches}]
        for attempt in range(3):
            r = requests.post(API_URL, headers=headers, json=body, timeout=600)
            if r.status_code in (429, 500, 502, 503, 529):
                wait = 20 * (attempt + 1)
                print(f"  API busy ({r.status_code}), retrying in {wait}s")
                time.sleep(wait)
                continue
            break
        if r.status_code != 200:
            raise RuntimeError(f"API error {r.status_code}: {r.text[:500]}")
        data = r.json()

        u = data.get("usage", {})
        usage_total["input_tokens"] += u.get("input_tokens", 0)
        usage_total["output_tokens"] += u.get("output_tokens", 0)
        usage_total["web_search_requests"] += (u.get("server_tool_use") or {}).get("web_search_requests", 0)

        for block in data.get("content", []):
            if block.get("type") == "text":
                texts.append(block.get("text", ""))

        if data.get("stop_reason") == "pause_turn":
            messages = [{"role": "user", "content": prompt},
                        {"role": "assistant", "content": data["content"]}]
            continue
        break

    print(f"  usage: {usage_total}")
    return "".join(texts)


def extract_json(text: str) -> dict:
    m = re.search(r"<brief>(.*?)(?:</brief>|$)", text, re.S)
    raw = m.group(1) if m else text
    start = raw.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in model output: {text[:300]!r}")
    raw = raw[start:]
    end = raw.rfind("}")
    try:
        return json.loads(raw[:end + 1])
    except json.JSONDecodeError:
        repaired = repair_truncated_json(raw)
        if repaired is None:
            raise
        print("  note: reply was cut off; kept the stories that were complete")
        return repaired


def repair_truncated_json(raw: str) -> dict | None:
    """If the model ran out of room mid-way, cut back to the last complete
    value and close any open brackets, so finished stories are not lost."""
    stack, in_str, esc, cut_points = [], False, False, []
    for i, ch in enumerate(raw):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
            cut_points.append((i + 1, list(stack)))
    for pos, open_stack in reversed(cut_points):
        candidate = raw[:pos].rstrip().rstrip(",") + "".join(reversed(open_stack))
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def clean_brief(data: dict, by_id: dict[str, dict] | None = None) -> dict:
    """Keep only well-formed items, in our section order.
    Cited headline ids become source links; ids that don't exist are dropped."""
    by_id = by_id or {}
    sec_by_id = {s.get("id"): s for s in data.get("sections", []) if isinstance(s, dict)}
    sections = []
    for spec in SECTIONS:
        src = sec_by_id.get(spec["id"], {})
        items = []
        for it in src.get("items", []) or []:
            if not isinstance(it, dict) or not str(it.get("headline", "")).strip():
                continue
            sources = []
            for sid in it.get("source_ids", []) or []:
                h = by_id.get(str(sid).strip("[] "))
                if h and h.get("link", "").startswith(("http://", "https://")):
                    sources.append({"name": (h.get("source") or "Source")[:60], "url": h["link"]})
            # direct links (only from web search or the mock file)
            sources += [
                {"name": str(s.get("name", "Source"))[:60], "url": s["url"]}
                for s in it.get("sources", []) or []
                if isinstance(s, dict) and str(s.get("url", "")).startswith(("http://", "https://"))
            ]
            uniq, seen_urls = [], set()
            for s in sources:
                if s["url"] not in seen_urls:
                    seen_urls.add(s["url"])
                    uniq.append(s)
            sources = uniq
            items.append({
                "headline": str(it.get("headline", "")).strip(),
                "summary": str(it.get("summary", "")).strip(),
                "detail": str(it.get("detail", "")).strip(),
                "why_it_matters": str(it.get("why_it_matters", "") or "").strip(),
                "sources": sources[:4],
            })
        sections.append({"id": spec["id"], "title": spec["title"], "items": items})

    snapshot = []
    for s in data.get("market_snapshot", []) or []:
        if isinstance(s, dict) and s.get("label") and s.get("value"):
            d = s.get("direction", "flat")
            snapshot.append({"label": str(s["label"]), "value": str(s["value"]),
                             "change": str(s.get("change", "")),
                             "direction": d if d in ("up", "down", "flat") else "flat"})
    return {"overview": str(data.get("overview", "")).strip(),
            "market_snapshot": snapshot[:6], "sections": sections}


# ─────────────────────────────────────────────────────────────
# 3. Save + archive
# ─────────────────────────────────────────────────────────────

def load_yesterday_headlines(out_dir: Path) -> list[str]:
    latest = out_dir / "latest.json"
    if not latest.exists():
        return []
    try:
        d = json.loads(latest.read_text(encoding="utf-8"))
        return [it["headline"] for s in d.get("sections", []) for it in s.get("items", [])]
    except Exception:
        return []


def save(brief: dict, out_dir: Path, keep_days: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    day = brief["date"]
    payload = json.dumps(brief, ensure_ascii=False, indent=1)
    (out_dir / f"{day}.json").write_text(payload, encoding="utf-8")
    (out_dir / "latest.json").write_text(payload, encoding="utf-8")

    # prune old days and rebuild the index
    cutoff = (datetime.now(TZ).date() - timedelta(days=keep_days)).isoformat()
    days = []
    for f in out_dir.glob("????-??-??.json"):
        if f.stem < cutoff:
            f.unlink()
        else:
            days.append(f.stem)
    (out_dir / "index.json").write_text(
        json.dumps({"days": sorted(days, reverse=True)}, indent=1), encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# 4. Optional phone alert (ntfy)
# ─────────────────────────────────────────────────────────────

def notify(brief: dict) -> None:
    topic = os.getenv("NTFY_TOPIC", "").strip()
    if not topic:
        return
    tops = [it["headline"] for it in brief["sections"][0]["items"][:3]]
    body = "\n".join(f"• {h}" for h in tops) or brief.get("overview", "Your brief is ready")
    headers = {"Title": "Your morning brief is ready", "Tags": "newspaper"}
    if os.getenv("APP_URL"):
        headers["Click"] = os.getenv("APP_URL")
    try:
        requests.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                      headers=headers, timeout=15)
        print("  ntfy alert sent")
    except Exception as e:
        print(f"  ! ntfy failed: {e}")


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true",
                    help="skip network and API; use tests/mock_output.txt")
    args = ap.parse_args()

    out_dir = Path(os.getenv("OUTPUT_DIR", "docs/data"))
    keep_days = int(os.getenv("KEEP_DAYS", "45"))
    now = datetime.now(TZ)
    today = now.date().isoformat()
    print(f"Morning brief for {today}")

    yesterday = load_yesterday_headlines(out_dir)

    by_id: dict[str, dict] = {}
    if args.mock:
        text = (Path(__file__).parent / "tests" / "mock_output.txt").read_text(encoding="utf-8")
        model_used = "mock"
    else:
        raw_key = os.getenv("ANTHROPIC_API_KEY", "")
        api_key = raw_key.strip().strip('"\'').strip()
        if not api_key:
            print("ANTHROPIC_API_KEY is not set")
            return 1
        print("Gathering headlines…")
        seeds, by_id = gather_seeds()
        if len(by_id) < 10:
            print("Too few headlines came back from Google News; keeping the previous brief")
            return 1
        max_searches = int(os.getenv("MAX_SEARCHES", "0") or 0)
        prompt = build_prompt(now.strftime("%A, %d %B %Y"), seeds, yesterday,
                              searches_enabled=max_searches > 0)
        models = [os.getenv("CLAUDE_MODEL", "claude-haiku-5-5"),
                  os.getenv("FALLBACK_MODEL", "claude-sonnet-5-5")]
        text, model_used, last_err = "", "", None
        for model in models:
            try:
                print(f"Asking {model}…")
                text = call_claude(prompt, model, api_key, max_searches)
                extract_json(text)  # validate before accepting
                model_used = model
                break
            except Exception as e:
                last_err = e
                print(f"  ! {model} failed: {e}")
        if not model_used:
            print(f"All models failed: {last_err}")
            return 1

    brief = clean_brief(extract_json(text), by_id)
    brief.update({"date": today, "generated_at": now.isoformat(timespec="minutes"),
                  "model": model_used})
    n = sum(len(s["items"]) for s in brief["sections"])
    if n == 0:
        print("Brief came back empty; keeping the previous one")
        return 1

    save(brief, out_dir, keep_days)
    print(f"Saved {n} stories to {out_dir}/{today}.json")
    if not args.mock:
        notify(brief)
    return 0


if __name__ == "__main__":
    sys.exit(main())
