#!/usr/bin/env python3
"""
Rebuild index.html for the Eureka Water office TV.

It does one thing: fetch a JSON blob that Claude keeps up to date in a Notion
page, drop it into template.html, and write index.html.

All the thinking (reading the jobs board, the quotes pipeline and the admin
calendar, deciding what is safe to show on a public screen) happens in the
scheduled Claude task. This script deliberately contains no business logic, so
there is only one place to change when the boards change.

Needs one environment variable:
    NOTION_TOKEN   an internal integration token with read access to the
                   "Eureka TV data" page and nothing else.
"""

import json
import os
import sys
import urllib.error
import urllib.request

DATA_PAGE_ID = "3d120e14-6bbd-81c2-b244-eeaad95281bc"          # set by setup, see README
NOTION_VERSION = "2022-06-28"
TEMPLATE = "template.html"
OUTPUT = "index.html"


def notion(path):
    req = urllib.request.Request(
        "https://api.notion.com/v1" + path,
        headers={
            "Authorization": "Bearer " + os.environ["NOTION_TOKEN"],
            "Notion-Version": NOTION_VERSION,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def find_json_block(page_id):
    """Walk the page's blocks and return the text of the first code block."""
    cursor = None
    while True:
        path = "/blocks/%s/children?page_size=100" % page_id
        if cursor:
            path += "&start_cursor=" + cursor
        payload = notion(path)

        for block in payload.get("results", []):
            if block.get("type") == "code":
                parts = block["code"].get("rich_text", [])
                return "".join(p.get("plain_text", "") for p in parts)

        if not payload.get("has_more"):
            return None
        cursor = payload.get("next_cursor")


def main():
    if "NOTION_TOKEN" not in os.environ:
        sys.exit("NOTION_TOKEN is not set. Add it as a repository secret.")

    if DATA_PAGE_ID.startswith("PASTE_"):
        sys.exit("DATA_PAGE_ID has not been filled in at the top of build.py.")

    try:
        raw = find_json_block(DATA_PAGE_ID)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        if e.code == 404:
            sys.exit(
                "Notion returned 404. The integration almost certainly has not "
                "been given access to the data page. Open the page, click the "
                "three dots, Connections, and add the integration.\n" + body
            )
        sys.exit("Notion returned HTTP %s: %s" % (e.code, body))

    if not raw:
        sys.exit("No code block found on the data page. Nothing to build.")

    # Fail before writing anything if the JSON is malformed, so a bad refresh
    # leaves yesterday's working dashboard on the wall rather than a blank one.
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit("The data page does not contain valid JSON: %s" % e)

    for key in ("generated", "jobs", "quotes", "today", "week"):
        if key not in data:
            sys.exit("The data is missing '%s'. Refusing to build." % key)

    # "insurance" was added after the first three screens, so it is optional on
    # purpose. A data page written before the scheduled task was updated still
    # builds, and the template drops the insurance screen from the rotation when
    # there is nothing in it. Once the task has run, this is always populated.
    if "insurance" not in data:
        print("Note: no 'insurance' in the data. "
              "The insurance screen will be skipped this build.")
        data["insurance"] = []

    template = open(TEMPLATE, encoding="utf-8").read()
    if "/*__DATA__*/{}" not in template:
        sys.exit("template.html has lost its /*__DATA__*/ marker.")

    page = template.replace(
        "/*__DATA__*/{}",
        json.dumps(data, ensure_ascii=False, indent=2),
    )

    open(OUTPUT, "w", encoding="utf-8").write(page)
    print("Built %s (%d bytes), data generated %s"
          % (OUTPUT, len(page), data.get("generated")))


if __name__ == "__main__":
    main()
