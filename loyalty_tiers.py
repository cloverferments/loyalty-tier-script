import os
import requests
from datetime import datetime, timedelta, timezone

STORE = os.environ["SHOPIFY_STORE"]
CLIENT_ID = os.environ["SHOPIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SHOPIFY_CLIENT_SECRET"]

BASE_URL = f"https://{STORE}.myshopify.com/admin/api/2026-07"

# Tier thresholds ($ spent in last 12 months)
PLATINUM_THRESHOLD = 1500
GOLD_THRESHOLD = 1000

TIER_TAGS = ["loyalty-silver", "loyalty-gold", "loyalty-platinum"]


def get_access_token():
    """Get access token using client credentials."""
    url = f"https://{STORE}.myshopify.com/admin/oauth/access_token"
    response = requests.post(url, json={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials"
    })
    # For custom apps, use client_id:client_secret as basic auth
    return None


def get_headers():
    return {
        "X-Shopify-Access-Token": get_token_via_admin(),
        "Content-Type": "application/json"
    }


def get_token_via_admin():
    """Custom apps use client secret directly as the access token."""
    return CLIENT_SECRET


def get_all_customers():
    """Fetch all customers from Shopify."""
    customers = []
    url = f"{BASE_URL}/customers.json?limit=250"
    headers = {
        "X-Shopify-Access-Token": CLIENT_SECRET,
        "Content-Type": "application/json"
    }

    while url:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        customers.extend(data.get("customers", []))

        # Handle pagination
        link_header = response.headers.get("Link", "")
        url = None
        if 'rel="next"' in link_header:
            for part in link_header.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")

    print(f"Found {len(customers)} customers")
    return customers


def get_customer_spend_last_12_months(customer_id, headers):
    """Calculate total spend for a customer in the last 12 months."""
    twelve_months_ago = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")

    orders = []
    url = f"{BASE_URL}/customers/{customer_id}/orders.json?status=any&limit=250&created_at_min={twelve_months_ago}"

    while url:
        response = requests.get(url, headers=headers)
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
    """Determine tier based on spend."""
    if spend >= PLATINUM_THRESHOLD:
        return "loyalty-platinum"
    elif spend >= GOLD_THRESHOLD:
        return "loyalty-gold"
    else:
        return "loyalty-silver"


def update_customer_tags(customer_id, current_tags, new_tier, headers):
    """Remove old tier tags and apply new one."""
    tags_list = [t.strip() for t in current_tags.split(",") if t.strip()]

    # Remove existing tier tags
    tags_list = [t for t in tags_list if t not in TIER_TAGS]

    # Add new tier
    tags_list.append(new_tier)
    new_tags = ", ".join(tags_list)

    url = f"{BASE_URL}/customers/{customer_id}.json"
    response = requests.put(url, headers=headers, json={
        "customer": {
            "id": customer_id,
            "tags": new_tags,
            "metafields": [
                {
                    "namespace": "custom",
                    "key": "loyalty_points",
                    "value": str(int(round(spend_cache.get(customer_id, 0)))),
                    "type": "single_line_text_field"
                }
            ]
        }
    })
    return response.status_code == 200


spend_cache = {}


def main():
    headers = {
        "X-Shopify-Access-Token": CLIENT_SECRET,
        "Content-Type": "application/json"
    }

    customers = get_all_customers()
    updated = 0
    errors = 0

    for customer in customers:
        customer_id = customer["id"]
        current_tags = customer.get("tags", "")
        email = customer.get("email", "unknown")

        spend = get_customer_spend_last_12_months(customer_id, headers)
        spend_cache[customer_id] = spend
        new_tier = calculate_tier(spend)

        # Check if update needed
        current_tier_tags = [t.strip() for t in current_tags.split(",") if t.strip() in TIER_TAGS]
        if current_tier_tags == [new_tier]:
            continue  # Already correct, skip

        success = update_customer_tags(customer_id, current_tags, new_tier, headers)
        if success:
            updated += 1
            print(f"✓ {email}: ${spend:.2f} → {new_tier}")
        else:
            errors += 1
            print(f"✗ Failed to update {email}")

    print(f"\nDone. Updated: {updated}, Errors: {errors}, Skipped: {len(customers) - updated - errors}")


if __name__ == "__main__":
    main()
