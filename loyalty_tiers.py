import os
import requests
import time
from datetime import datetime, timedelta, timezone

STORE = os.environ["SHOPIFY_STORE"]
CLIENT_SECRET = os.environ["SHOPIFY_CLIENT_SECRET"]

BASE_URL = f"https://{STORE}.myshopify.com/admin/api/2026-07"

PLATINUM_THRESHOLD = 1500
GOLD_THRESHOLD = 1000
TIER_TAGS = ["loyalty-silver", "loyalty-gold", "loyalty-platinum"]

spend_cache = {}


def get_headers():
    return {
        "X-Shopify-Access-Token": CLIENT_SECRET,
        "Content-Type": "application/json"
    }


def make_request(method, url, **kwargs):
    """Make a request with rate limit handling."""
    headers = get_headers()
    for attempt in range(5):
        if method == "get":
            response = requests.get(url, headers=headers, **kwargs)
        elif method == "put":
            response = requests.put(url, headers=headers, **kwargs)
        elif method == "post":
            response = requests.post(url, headers=headers, **kwargs)

        if response.status_code == 429:
            retry_after = int(float(response.headers.get("Retry-After", 2)))
            print(f"Rate limited, waiting {retry_after}s...")
            time.sleep(retry_after)
            continue
        return response
    return response


def get_all_customers():
    customers = []
    url = f"{BASE_URL}/customers.json?limit=250"

    while url:
        response = make_request("get", url)
        response.raise_for_status()
        data = response.json()
        customers.extend(data.get("customers", []))

        link_header = response.headers.get("Link", "")
        url = None
        if 'rel="next"' in link_header:
            for part in link_header.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")

        time.sleep(0.5)

    print(f"Found {len(customers)} customers")
    return customers


def get_customer_spend_last_12_months(customer_id):
    twelve_months_ago = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")

    orders = []
    url = f"{BASE_URL}/customers/{customer_id}/orders.json?status=any&limit=250&created_at_min={twelve_months_ago}"

    while url:
        response = make_request("get", url)
        if response.status_code != 200:
            return 0
        data = response.json()
        orders.extend(data.get("orders", []))

        link_header = response.headers.get("Link", "")
        url = None
        if 'rel="next"' in link_header:
            for part in link_header.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")

    total = sum(float(order.get("total_price", 0)) for order in orders
                if order.get("financial_status") in ["paid", "partially_refunded"])
    return total


def calculate_tier(spend):
    if spend >= PLATINUM_THRESHOLD:
        return "loyalty-platinum"
    elif spend >= GOLD_THRESHOLD:
        return "loyalty-gold"
    else:
        return "loyalty-silver"


def update_customer_tags(customer_id, current_tags, new_tier, spend):
    tags_list = [t.strip() for t in current_tags.split(",") if t.strip()]
    tags_list = [t for t in tags_list if t not in TIER_TAGS]
    tags_list.append(new_tier)
    new_tags = ", ".join(tags_list)

    url = f"{BASE_URL}/customers/{customer_id}.json"
    payload = {"customer": {"id": customer_id, "tags": new_tags}}
    response = make_request("put", url, json=payload)

    if response.status_code == 200:
        # Update loyalty points metafield
        metafield_url = f"{BASE_URL}/customers/{customer_id}/metafields.json"
        metafield_payload = {
            "metafield": {
                "namespace": "custom",
                "key": "loyalty_points",
                "value": str(int(round(spend))),
                "type": "single_line_text_field"
            }
        }
        make_request("post", metafield_url, json=metafield_payload)
        time.sleep(0.5)
        return True
    return False


def main():
    # Test connection
    test_response = make_request("get", f"{BASE_URL}/shop.json")
    if test_response.status_code != 200:
        print(f"ERROR: Could not connect. Status: {test_response.status_code}")
        raise SystemExit(1)
    print(f"✓ Connected to Shopify store: {STORE}")

    customers = get_all_customers()
    updated = 0
    errors = 0
    skipped = 0

    for i, customer in enumerate(customers):
        customer_id = customer["id"]
        current_tags = customer.get("tags", "")
        email = customer.get("email", "unknown")

        spend = get_customer_spend_last_12_months(customer_id)
        new_tier = calculate_tier(spend)

        current_tier_tags = [t.strip() for t in current_tags.split(",") if t.strip() in TIER_TAGS]
        if current_tier_tags == [new_tier]:
            skipped += 1
            continue

        success = update_customer_tags(customer_id, current_tags, new_tier, spend)
        if success:
            updated += 1
            print(f"✓ {email}: ${spend:.2f} → {new_tier}")
        else:
            errors += 1
            print(f"✗ Failed to update {email}")

        # Progress update every 100 customers
        if (i + 1) % 100 == 0:
            print(f"Progress: {i + 1}/{len(customers)} — Updated: {updated}, Errors: {errors}, Skipped: {skipped}")

    print(f"\nDone. Updated: {updated}, Errors: {errors}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
