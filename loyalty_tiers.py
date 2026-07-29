import os
import requests
import time
from datetime import datetime, timedelta, timezone

STORE = os.environ["SHOPIFY_STORE"]
CLIENT_SECRET = os.environ["SHOPIFY_CLIENT_SECRET"]

BASE_URL = f"https://{STORE}.myshopify.com/admin/api/2026-07"

TEST_EMAIL = "kiwipal2@gmail.com"


def get_headers():
    return {
        "X-Shopify-Access-Token": CLIENT_SECRET,
        "Content-Type": "application/json"
    }


def make_request(url):
    headers = get_headers()
    for attempt in range(5):
        response = requests.get(url, headers=headers)
        if response.status_code == 429:
            retry_after = int(float(response.headers.get("Retry-After", 2)))
            print(f"Rate limited, waiting {retry_after}s...")
            time.sleep(retry_after)
            continue
        return response
    return response


def main():
    # Find customer by email
    url = f"{BASE_URL}/customers/search.json?query=email:{TEST_EMAIL}"
    response = make_request(url)
    customers = response.json().get("customers", [])
    
    if not customers:
        print(f"Customer {TEST_EMAIL} not found")
        return
    
    customer = customers[0]
    customer_id = customer["id"]
    print(f"Found customer: {customer['first_name']} {customer['last_name']} (ID: {customer_id})")
    print(f"Current tags: {customer.get('tags', 'none')}")
    
    # Fetch orders from last 12 months
    twelve_months_ago = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\nFetching orders since: {twelve_months_ago}")
    
    total = 0.0
    order_count = 0
    skipped_count = 0
    page_count = 0
    
    url = f"{BASE_URL}/orders.json?customer_id={customer_id}&status=any&limit=250&created_at_min={twelve_months_ago}"
    
    while url:
        response = make_request(url)
        orders = response.json().get("orders", [])
        page_count += 1
        print(f"\nPage {page_count}: {len(orders)} orders")
        
        for order in orders:
            financial_status = order.get("financial_status", "")
            order_total = float(order.get("total_price", 0))
            order_name = order.get("name", "?")
            created_at = order.get("created_at", "?")[:10]
            
            if financial_status in ["paid", "partially_refunded"]:
                total += order_total
                order_count += 1
                print(f"  ✓ {order_name} ({created_at}): ${order_total} [{financial_status}]")
            else:
                skipped_count += 1
                print(f"  ✗ SKIPPED {order_name} ({created_at}): ${order_total} [{financial_status}]")
        
        link_header = response.headers.get("Link", "")
        url = None
        if 'rel="next"' in link_header:
            for part in link_header.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
            print("Fetching next page...")
        
        time.sleep(0.3)
    
    print(f"\n{'='*50}")
    print(f"Total pages: {page_count}")
    print(f"Orders counted: {order_count}")
    print(f"Orders skipped: {skipped_count}")
    print(f"Total spend: ${total:.2f}")
    
    if total >= 1500:
        print("Tier: PLATINUM")
    elif total >= 1000:
        print("Tier: GOLD")
    else:
        print("Tier: SILVER")


if __name__ == "__main__":
    main()
