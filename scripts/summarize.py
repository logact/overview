#!/usr/bin/env python3
"""Summarize recent GitHub repo activity (issues/PRs + comments) with an LLM
and create a `summarization`-labeled issue with the report.

Pipeline: fetch -> compress -> LLM -> create issue.

Env vars:
  GITHUB_TOKEN       - token with issues:write on the repo (falls back to `gh auth token`)
  GITHUB_REPOSITORY  - "owner/repo" (falls back to `gh repo view`)
  MOONSHOT_API_KEY   - Kimi/Moonshot API key (LLM step is skipped if absent)
  MOONSHOT_MODEL     - override model name (default: kimi-latest)

Flags:
  --window-hours N   - activity window in hours (default 6)
  --dry-run          - print compressed payload and LLM result, do not create an issue
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

GITHUB_API = "https://api.github.com"
MOONSHOT_URL = "https://api.moonshot.ai/v1/chat/completions"
SUMMARY_LABEL = "summarization"

MAX_ITEMS = 50
MAX_PAYLOAD_BYTES = 60_000
MAX_EXCERPT_CHARS = 800
MAX_CODE_FENCE_LINES = 20


def gh_api(path, token, method="GET", body=None):
    url = path if path.startswith("http") else GITHUB_API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"GitHub API {method} {url} failed: {e.code} {e.read().decode()[:500]}")


def gh_api_paginated(path, token):
    items, sep = [], "&" if "?" in path else "?"
    page = 1
    while True:
        batch = gh_api(f"{path}{sep}per_page=100&page={page}", token)
        if not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def get_token():
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    return subprocess.check_output(["gh", "auth", "token"], text=True).strip()


def get_repo():
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        return repo
    out = subprocess.check_output(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], text=True
    )
    return out.strip()


# ---------- compression ----------

_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
_WS_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{3,}")
_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def _shrink_fence(m):
    lines = m.group(1).splitlines()
    if len(lines) <= MAX_CODE_FENCE_LINES:
        return m.group(0)
    kept = "\n".join(lines[:MAX_CODE_FENCE_LINES])
    return f"```\n{kept}\n... ({len(lines) - MAX_CODE_FENCE_LINES} lines omitted)\n```"


def compress_text(text):
    if not text:
        return ""
    text = _IMG_RE.sub("[image]", text)
    text = _BASE64_RE.sub("[base64-data]", text)
    text = _FENCE_RE.sub(_shrink_fence, text)
    text = _WS_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text).strip()
    if len(text) > MAX_EXCERPT_CHARS:
        text = text[:MAX_EXCERPT_CHARS] + "… [truncated]"
    return text


def build_payload(repo, token, since):
    """Fetch issues+PRs updated since `since`, compress into compact JSONL."""
    issues = gh_api_paginated(
        f"/repos/{repo}/issues?state=all&since={since}&sort=updated&direction=desc", token
    )
    records = []
    for item in issues:
        labels = [l["name"] if isinstance(l, dict) else l for l in item.get("labels", [])]
        if SUMMARY_LABEL in labels:
            continue  # never summarize our own past reports
        is_pr = "pull_request" in item
        record = {
            "type": "pr" if is_pr else "issue",
            "number": item["number"],
            "title": item.get("title", ""),
            "author": (item.get("user") or {}).get("login", "?"),
            "state": item.get("state", "?"),
            "updated_at": item.get("updated_at", ""),
            "excerpt": compress_text(item.get("body")),
        }
        comments = gh_api_paginated(
            f"/repos/{repo}/issues/{item['number']}/comments?since={since}", token
        )
        if is_pr:
            comments += gh_api_paginated(
                f"/repos/{repo}/pulls/{item['number']}/comments?since={since}", token
            )
        record["new_comments"] = [
            {"author": (c.get("user") or {}).get("login", "?"), "excerpt": compress_text(c.get("body"))}
            for c in comments
        ]
        if not record["excerpt"] and not record["new_comments"]:
            continue  # nothing meaningful to report
        records.append(record)

    omitted = 0
    if len(records) > MAX_ITEMS:
        omitted = len(records) - MAX_ITEMS
        records = records[:MAX_ITEMS]  # already sorted by updated desc

    # enforce total payload cap by dropping oldest-updated records
    payload = [json.dumps(r, ensure_ascii=False) for r in records]
    while payload and sum(len(p) for p in payload) > MAX_PAYLOAD_BYTES:
        payload.pop()
        omitted += 1

    if omitted:
        payload.append(json.dumps({"note": f"{omitted} older item(s) omitted due to size cap"}))
    return "\n".join(payload), len(records)


# ---------- LLM ----------

def call_llm(payload, since):
    api_key = os.environ.get("MOONSHOT_API_KEY")
    if not api_key:
        return None
    model = os.environ.get("MOONSHOT_MODEL", "kimi-latest")
    body = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You summarize recent GitHub repository activity (issues, pull requests and "
                    "their comments) into a concise work report for the repo owner. "
                    "Respond with a single JSON object: "
                    '{"title": "<brief report title, max 80 chars>", '
                    '"report": "<full markdown report>"}. '
                    "The report should group related work, highlight decisions and open questions, "
                    "and reference items as #number. Write in the dominant language of the input."
                ),
            },
            {
                "role": "user",
                "content": f"Activity since {since} (compressed JSONL):\n{payload}",
            },
        ],
    }
    req = urllib.request.Request(MOONSHOT_URL, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Moonshot API failed: {e.code} {e.read().decode()[:500]}")
    content = data["choices"][0]["message"]["content"]
    result = json.loads(content)
    return result.get("title", "").strip(), result.get("report", "").strip()


# ---------- issue creation ----------

def ensure_label(repo, token):
    existing = gh_api(f"/repos/{repo}/labels/{SUMMARY_LABEL}", token)
    if existing is None:
        gh_api(
            f"/repos/{repo}/labels",
            token,
            method="POST",
            body={"name": SUMMARY_LABEL, "color": "1D76DB",
                  "description": "Automated periodic work summary"},
        )


def create_issue(repo, token, title, report, since, count):
    ensure_label(repo, token)
    footer = (
        f"\n\n---\n*Auto-generated summary of activity since {since} "
        f"({count} item(s)). Triggered by the scheduled summarization workflow.*"
    )
    issue = gh_api(
        f"/repos/{repo}/issues",
        token,
        method="POST",
        body={"title": title, "body": report + footer, "labels": [SUMMARY_LABEL]},
    )
    return issue["html_url"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-hours", type=float, default=6)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = get_token()
    repo = get_repo()
    since = (datetime.now(timezone.utc) - timedelta(hours=args.window_hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    print(f"Repo: {repo} | window: since {since}", file=sys.stderr)

    payload, count = build_payload(repo, token, since)
    if not payload:
        print("No activity in window; nothing to summarize.", file=sys.stderr)
        return 0

    print(f"Compressed payload: {count} item(s), {len(payload.encode())} bytes", file=sys.stderr)
    if args.dry_run:
        print("--- compressed payload ---")
        print(payload)
        print("--------------------------")

    llm = call_llm(payload, since)
    if llm is None:
        print("MOONSHOT_API_KEY not set; skipping LLM call.", file=sys.stderr)
        return 0
    title, report = llm
    if not title:
        title = f"Work summary {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"

    if args.dry_run:
        print(f"--- LLM title ---\n{title}\n--- LLM report ---\n{report}")
        return 0

    url = create_issue(repo, token, title, report, since, count)
    print(f"Created summary issue: {url}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
