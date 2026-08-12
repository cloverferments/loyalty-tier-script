import os
import csv
import requests
import time
from collections import defaultdict

CLIENT_SECRET = os.environ["SHOPIFY_CLIENT_SECRET"]
STORE = os.environ["SHOPIFY_STORE"]
REST_URL = f"https://{STORE}.myshopify.com/admin/api/2026-07"

PLATINUM_THRESHOLD = 1500
GOLD_THRESHOLD = 1000
TIER_TAGS = ["loyalty-silver", "loyalty-gold", "loyalty-platinum"]
CSV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orders.csv")
TEST_MODE = False  # Set to False to process all customers


def get_headers():
    return {
        "X-Shopify-Access-Token": CLIENT_SECRET,
        "Content-Type": "application/json"
    }


def make_request(method, url, **kwargs):
    for attempt in range(5):
        if method == "get":
            response = requests.get(url, headers=get_headers(), **kwargs)
        elif method == "put":
            response = requests.put(url, headers=get_headers(), **kwargs)
        elif method == "post":
            response = requests.post(url, headers=get_headers(), **kwargs)

        if response.status_code == 429:
            retry_after = int(float(response.headers.get("Retry-After", 2)))
            print(f"Rate limited, waiting {retry_after}s...")
            time.sleep(retry_after)
            continue
        return response
    return response


def calculate_spend_from_csv():
    customer_spend = defaultdict(float)
    seen_orders = set()

    with open(CSV_FILE, "rb") as f:
        raw = f.read()

    print(f"CSV file size: {len(raw)} bytes")
    print(f"First 100 bytes: {raw[:100]}")

    if raw.startswith(b'\xef\xbb\xbf'):
        raw = raw[3:]
        print("BOM removed")

    content = raw.decode("utf-8").replace('\r\n', '\n').replace('\r', '\n')
    lines = content.splitlines()
    print(f"Total lines in CSV: {len(lines)}")
    print(f"Header line: {lines[0][:100] if lines else 'EMPTY'}")

    reader = csv.DictReader(lines)
    print(f"CSV columns detected: {reader.fieldnames}")

    row_count = 0
    for row in reader:
        row_count += 1
        order_name = (row.get("Name") or "").strip()
        email = (row.get("Email") or "").strip().lower()
        financial_status = (row.get("Financial Status") or "").strip().lower()
        total = (row.get("Total") or "0").strip()
        paid_at = (row.get("Paid at") or "").strip()

        if not email or not order_name:
            continue
        if order_name in seen_orders:
            continue
        if financial_status not in ["paid", "partially_refunded"]:
            continue
        if not paid_at:
            continue

        try:
            amount = float(total)
        except ValueError:
            continue

        seen_orders.add(order_name)
        customer_spend[email] += amount

    print(f"Total rows read: {row_count}")
    print(f"Processed {len(seen_orders)} orders for {len(customer_spend)} customers")
    return customer_spend


def get_customers_by_email(emails):
    """Fetch specific customers by email."""
    customers = []
    for email in emails:
        response = make_request("get", f"{REST_URL}/customers/search.json?query=email:{email}&limit=1")
        results = response.json().get("customers", [])
        if results:
            customers.append(results[0])
        time.sleep(0.3)
    print(f"Found {len(customers)} matching customers in Shopify")
    return customers


def get_all_customers():
    customers = []
    url = f"{REST_URL}/customers.json?limit=250"

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

    print(f"Found {len(customers)} customers in Shopify")
    return customers


def calculate_tier(spend):
    if spend >= PLATINUM_THRESHOLD:
        return "loyalty-platinum"
    elif spend >= GOLD_THRESHOLD:
        return "loyalty-gold"
    else:
        return "loyalty-silver"


def update_customer(customer_id, current_tags, new_tier, spend):
    tags_list = [t.strip() for t in current_tags.split(",") if t.strip()]
    tags_list = [t for t in tags_list if t not in TIER_TAGS]
    tags_list.append(new_tier)
    new_tags = ", ".join(tags_list)

    url = f"{REST_URL}/customers/{customer_id}.json"
    payload = {"customer": {"id": customer_id, "tags": new_tags}}
    response = make_request("put", url, json=payload)

    if response.status_code == 200:
        metafield_url = f"{REST_URL}/customers/{customer_id}/metafields.json"
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
    test_response = make_request("get", f"{REST_URL}/shop.json")
    if test_response.status_code != 200:
        print(f"ERROR: Could not connect. Status: {test_response.status_code}")
        raise SystemExit(1)
    print(f"✓ Connected to Shopify store: {STORE}")

    print(f"\nReading {CSV_FILE}...")
    customer_spend = calculate_spend_from_csv()

    top = sorted(customer_spend.items(), key=lambda x: x[1], reverse=True)[:10]
    print("\nTop 10 spenders from CSV:")
    for email, spend in top:
        print(f"  {email}: ${spend:.2f}")

    if TEST_MODE:
        # Only fetch and update platinum customers
        platinum_emails = [email for email, spend in customer_spend.items() if spend >= PLATINUM_THRESHOLD]
        print(f"\nTEST MODE: Processing {len(platinum_emails)} platinum customers only")
        customers = get_customers_by_email(platinum_emails)
    else:
        print("\nFetching all customers from Shopify...")
        customers = get_all_customers()

    updated = 0
    errors = 0
    skipped = 0

    for i, customer in enumerate(customers):
        customer_id = customer["id"]
        current_tags = customer.get("tags", "")
        email = (customer.get("email") or "").lower()

        spend = customer_spend.get(email, 0.0)
        new_tier = calculate_tier(spend)

        current_tier_tags = [t.strip() for t in current_tags.split(",") if t.strip() in TIER_TAGS]
        if current_tier_tags == [new_tier]:
            skipped += 1
            print(f"  skipped {email} (already {new_tier})")
            continue

        success = update_customer(customer_id, current_tags, new_tier, spend)
        if success:
            updated += 1
            print(f"✓ {email}: ${spend:.2f} → {new_tier}")
        else:
            errors += 1
            print(f"✗ Failed to update {email}")

    print(f"\nDone. Updated: {updated}, Errors: {errors}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
