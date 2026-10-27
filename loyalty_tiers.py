"""
Clover Ferments — weekly loyalty tier update (runs on GitHub Actions)

No CSV needed: pulls the last 12 months of paid orders straight from Shopify,
adds up each customer's spend, and updates only the customers whose tier or
points have changed.

Tiers (12-month spend): Silver < $1,000 · Gold $1,000–$1,499 · Platinum $1,500+

Environment (GitHub secrets):
  SHOPIFY_STORE          e.g. cloverferments
  SHOPIFY_CLIENT_ID      app Client ID
  SHOPIFY_CLIENT_SECRET  app Client secret
  DRY_RUN                "true" = report only, change nothing
"""

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

def _store_name(raw):
    """Accepts 'cloverferments', 'cloverferments.myshopify.com' or a full admin URL."""
    s = (raw or "").strip().lower()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    if s.startswith("admin.shopify.com/store/"):
        s = s[len("admin.shopify.com/store/"):]
    s = s.split("/")[0]
    if s.endswith(".myshopify.com"):
        s = s[: -len(".myshopify.com")]
    return s


STORE = _store_name(os.environ.get("SHOPIFY_STORE", ""))
CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()
DRY_RUN = os.environ.get("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")

API_VERSION = "2026-07"
BASE = f"https://{STORE}.myshopify.com"
REST = f"{BASE}/admin/api/{API_VERSION}"
GRAPHQL = f"{REST}/graphql.json"

PLATINUM = 1500
GOLD = 1000
TIER_TAGS = ["loyalty-silver", "loyalty-gold", "loyalty-platinum"]
COUNTED_STATUSES = {"PAID", "PARTIALLY_REFUNDED"}
WINDOW_DAYS = 365

session = requests.Session()
summary_lines = []


def log(msg=""):
    print(msg, flush=True)


def summary(msg=""):
    """Also written to the GitHub run summary page."""
    log(msg)
    summary_lines.append(msg)


# ---------------------------------------------------------------- auth

def get_access_token():
    """Newer Shopify apps swap Client ID + secret for a short-lived token.
    Older setups stored an access token directly in SHOPIFY_CLIENT_SECRET."""
    if CLIENT_ID:
        r = session.post(
            f"{BASE}/admin/oauth/access_token",
            data={"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        data = safe_json(r)
        if r.ok and data.get("access_token"):
            log("✓ Logged in with Client ID + secret")
            return data["access_token"]
        raise SystemExit(
            f"ERROR: Shopify login failed (HTTP {r.status_code}). The SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET "
            f"secrets in GitHub probably don't match the app's current Client ID and secret (the ones in Cloudflare). "
            f"Shopify replied: {describe(r)}"
        )
    return CLIENT_SECRET


def safe_json(r):
    try:
        return r.json()
    except ValueError:
        return {}


def describe(r):
    """Short, secret-free description of an unexpected Shopify reply."""
    ctype = r.headers.get("Content-Type", "")
    text = (r.text or "").strip().replace("\n", " ")
    if "html" in ctype or text.startswith("<"):
        title = ""
        low = text.lower()
        if "<title>" in low:
            title = text[low.index("<title>") + 7: low.index("</title>")] if "</title>" in low else ""
        return f"a web page instead of data ({title.strip()[:80] or 'no title'}) — url {r.url.split('?')[0]}"
    return text[:200] or "(empty reply)"


def request(method, url, **kwargs):
    for attempt in range(8):
        r = session.request(method, url, timeout=60, **kwargs)
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("Retry-After", 2 + attempt * 2))
            log(f"  Shopify busy ({r.status_code}) — waiting {wait:.0f}s")
            time.sleep(wait)
            continue
        return r
    return r


def graphql(query, variables=None):
    for attempt in range(8):
        r = request("POST", GRAPHQL, json={"query": query, "variables": variables or {}})
        if r.status_code in (401, 403):
            raise SystemExit(f"ERROR: Shopify refused access ({r.status_code}). Check the app's permissions and the GitHub secrets.\n{r.text[:300]}")
        data = safe_json(r)
        if not r.ok or not data:
            raise RuntimeError(f"Shopify GraphQL request failed (HTTP {r.status_code}): {describe(r)}")
        errors = data.get("errors")
        if errors:
            if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors):
                time.sleep(2 + attempt * 2)
                continue
            raise RuntimeError(f"GraphQL error: {json.dumps(errors)[:500]}")
        return data["data"]
    raise RuntimeError("Shopify kept throttling GraphQL requests")


# ---------------------------------------------------------------- bulk export

def run_bulk_query(inner_query, label):
    """Ask Shopify to export everything matching the query to one file, then download it."""
    started = graphql(
        """mutation($q: String!) {
             bulkOperationRunQuery(query: $q) { bulkOperation { id status } userErrors { field message } }
           }""",
        {"q": inner_query},
    )["bulkOperationRunQuery"]
    if started["userErrors"]:
        raise RuntimeError(f"Couldn't start {label} export: {started['userErrors']}")
    op_id = started["bulkOperation"]["id"]
    log(f"  {label} export started")

    for _ in range(240):  # up to ~40 minutes
        time.sleep(10)
        op = graphql(
            """query($id: ID!) { node(id: $id) { ... on BulkOperation { status errorCode objectCount url } } }""",
            {"id": op_id},
        )["node"]
        if op["status"] == "COMPLETED":
            log(f"  {label} export ready ({op['objectCount']} rows)")
            if not op["url"]:
                return []
            return download_jsonl(op["url"], label)
        if op["status"] in ("FAILED", "CANCELED", "EXPIRED"):
            raise RuntimeError(f"{label} export {op['status']} ({op.get('errorCode')})")
    raise RuntimeError(f"{label} export took too long")


def download_jsonl(url, label):
    """The export is a file link on Shopify's storage — fetch it WITHOUT the Shopify login headers."""
    for attempt in range(5):
        r = requests.get(url, timeout=300)
        if r.ok:
            break
        time.sleep(3 + attempt * 3)
    if not r.ok:
        raise RuntimeError(f"Couldn't download the {label} export (HTTP {r.status_code}): {describe(r)}")
    rows = []
    for n, line in enumerate(r.text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            raise RuntimeError(f"The {label} export file wasn't in the expected format (line {n}): {line[:120]}")
    log(f"  {label} export downloaded ({len(rows)} rows)")
    return rows


def twelve_month_spend():
    since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = run_bulk_query(
        f"""{{ orders(query: "processed_at:>='{since}'") {{ edges {{ node {{
              id name email processedAt displayFinancialStatus
              totalPriceSet {{ shopMoney {{ amount }} }}
              customer {{ id email }}
            }} }} }} }}""",
        "Orders",
    )
    spend = defaultdict(float)
    seen = set()
    counted = 0
    for o in rows:
        if o.get("name") in seen:
            continue
        if (o.get("displayFinancialStatus") or "").upper() not in COUNTED_STATUSES:
            continue
        email = ((o.get("customer") or {}).get("email") or o.get("email") or "").strip().lower()
        if not email:
            continue
        try:
            amount = float(o["totalPriceSet"]["shopMoney"]["amount"])
        except (KeyError, TypeError, ValueError):
            continue
        seen.add(o.get("name"))
        spend[email] += amount
        counted += 1
    summary(f"- **{counted}** paid orders in the last 12 months from **{len(spend)}** customers")
    return spend


def all_customers():
    rows = run_bulk_query(
        """{ customers { edges { node {
              id email tags
              points: metafield(namespace: "custom", key: "loyalty_points") { value }
            } } } }""",
        "Customers",
    )
    return rows


# ---------------------------------------------------------------- tiers

def tier_for(spend):
    if spend >= PLATINUM:
        return "loyalty-platinum"
    if spend >= GOLD:
        return "loyalty-gold"
    return "loyalty-silver"


def numeric_id(gid):
    return gid.rsplit("/", 1)[-1]


def update_tags(customer, new_tier):
    tags = [t.strip() for t in customer.get("tags") or [] if t.strip() and t.strip() not in TIER_TAGS]
    tags.append(new_tier)
    r = request(
        "PUT",
        f"{REST}/customers/{numeric_id(customer['id'])}.json",
        json={"customer": {"id": int(numeric_id(customer["id"])), "tags": ", ".join(tags)}},
    )
    time.sleep(0.5)
    return r.ok


def set_points(batch):
    """batch: list of (customer gid, points string). Up to 25 per call."""
    result = graphql(
        """mutation($m: [MetafieldsSetInput!]!) {
             metafieldsSet(metafields: $m) { userErrors { field message } }
           }""",
        {"m": [
            {"ownerId": gid, "namespace": "custom", "key": "loyalty_points", "type": "single_line_text_field", "value": pts}
            for gid, pts in batch
        ]},
    )["metafieldsSet"]
    return result["userErrors"]


def main():
    started = datetime.now()
    summary(f"## Loyalty tier update — {started:%a %d %b %Y}")
    summary("**TEST RUN — nothing will be changed**" if DRY_RUN else "Live run")

    global session
    token = get_access_token()
    session.headers.update({"X-Shopify-Access-Token": token, "Content-Type": "application/json"})
    shop = request("GET", f"{REST}/shop.json")
    shop_data = safe_json(shop)
    if not shop.ok or "shop" not in shop_data:
        raise SystemExit(f"ERROR: Could not connect to Shopify store '{STORE}' (HTTP {shop.status_code}). Shopify replied: {describe(shop)}")
    log(f"✓ Connected to {shop_data['shop']['name']}")

    log("\nReading 12 months of orders...")
    spend = twelve_month_spend()
    log("\nReading customers...")
    customers = all_customers()

    counts = {t: 0 for t in TIER_TAGS}
    tier_changes = []
    points_updates = []
    for c in customers:
        email = (c.get("email") or "").strip().lower()
        s = spend.get(email, 0.0)
        new_tier = tier_for(s)
        counts[new_tier] += 1
        current = [t for t in (c.get("tags") or []) if t in TIER_TAGS]
        if current != [new_tier]:
            tier_changes.append((c, current[0] if current else "none", new_tier, s))
        points = str(int(round(s)))
        old_points = (c.get("points") or {}).get("value")
        if old_points != points and not (old_points is None and points == "0"):
            points_updates.append((c["id"], points))

    summary(f"- Tiers: **{counts['loyalty-platinum']} platinum** · **{counts['loyalty-gold']} gold** · {counts['loyalty-silver']} silver")
    moves = [x for x in tier_changes if x[1] != "none" or x[2] != "loyalty-silver"]
    summary(f"- Tier changes: **{len(tier_changes)}** ({len(moves)} into or between gold/platinum, the rest new silver tags)")
    summary(f"- Points to update: **{len(points_updates)}**")

    if moves:
        summary("\n| Customer | From | To | 12-month spend |\n|---|---|---|---|")
        for c, old, new, s in sorted(moves, key=lambda x: -x[3])[:200]:
            summary(f"| {c.get('email') or numeric_id(c['id'])} | {old.replace('loyalty-', '')} | {new.replace('loyalty-', '')} | ${s:,.2f} |")

    if DRY_RUN:
        summary("\nTest run finished — nothing was changed.")
        return

    log("\nUpdating tiers...")
    failed = 0
    for i, (c, old, new, s) in enumerate(tier_changes, 1):
        if not update_tags(c, new):
            failed += 1
            log(f"  ✗ couldn't update {c.get('email')}")
        if i % 100 == 0:
            log(f"  {i}/{len(tier_changes)}")

    log("Updating points...")
    point_errors = 0
    for i in range(0, len(points_updates), 25):
        errs = set_points(points_updates[i:i + 25])
        if errs:
            point_errors += len(errs)
            log(f"  ! {errs[:2]}")

    summary(f"\nDone in {int((datetime.now() - started).total_seconds() // 60)} min — "
            f"{len(tier_changes) - failed} tiers updated, {failed} failed, {len(points_updates)} points updated ({point_errors} errors)")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    try:
        if not STORE:
            raise SystemExit("ERROR: The SHOPIFY_STORE secret is missing in GitHub (Settings → Secrets and variables → Actions).")
        if not CLIENT_SECRET:
            raise SystemExit("ERROR: The SHOPIFY_CLIENT_SECRET secret is missing in GitHub (Settings → Secrets and variables → Actions).")
        main()
    except SystemExit as e:
        if e.code not in (0, None):
            summary(f"\n**❌ Stopped:** {e.code}")
        raise
    except Exception as e:
        summary(f"\n**❌ Stopped:** {type(e).__name__}: {str(e)[:500]}")
        raise
    finally:
        path = os.environ.get("GITHUB_STEP_SUMMARY")
        if path and summary_lines:
            with open(path, "a") as f:
                f.write("\n".join(summary_lines) + "\n")
