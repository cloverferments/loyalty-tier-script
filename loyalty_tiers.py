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
            time.sleep(retry_after)
            continue
        return response
    return response


def main():
    # Find customer
    url = f"{BASE_URL}/customers/search.json?query=email:{TEST_EMAIL}"
    response = make_request(url)
    customers = response.json().get("customers", [])

    if not customers:
        print(f"Customer {TEST_EMAIL} not found")
        return

    customer = customers[0]
    customer_id = customer["id"]
    print(f"Found: {customer['first_name']} {customer['last_name']} (ID: {customer_id})")

    twelve_months_ago = datetime.now(timezone.utc) - timedelta(days=365)

    # Try 3 different API approaches and compare results
    approaches = [
        f"{BASE_URL}/customers/{customer_id}/orders.json?status=any&limit=250",
        f"{BASE_URL}/orders.json?customer_id={customer_id}&status=any&limit=250",
        f"{BASE_URL}/customers/{customer_id}/orders.json?limit=250",
    ]

    for i, start_url in enumerate(approaches):
        print(f"\n{'='*50}")
        print(f"APPROACH {i+1}: {start_url}")
        
        total = 0.0
        order_count = 0
        page_count = 0
        url = start_url

        while url:
            response = make_request(url)
            orders = response.json().get("orders", [])
            page_count += 1
            
            # Print raw link header
            link_header = response.headers.get("Link", "NO LINK HEADER")
            print(f"Page {page_count}: {len(orders)} orders | Link header: {link_header[:100] if len(link_header) > 100 else link_header}")

            for order in orders:
                created_at_str = order.get("created_at", "")
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                financial_status = order.get("financial_status", "")
                order_total = float(order.get("total_price", 0))
                order_name = order.get("name", "?")

                if created_at < twelve_months_ago:
                    print(f"  STOP at {order_name} ({created_at.strftime('%Y-%m-%d')}) — older than 12 months")
                    url = None
                    break

                if financial_status in ["paid", "partially_refunded"]:
                    total += order_total
                    order_count += 1
                    print(f"  ✓ {order_name} ({created_at.strftime('%Y-%m-%d')}): ${order_total}")
                else:
                    print(f"  ✗ SKIP {order_name}: {financial_status}")
            else:
                # Only paginate if we didn't break early
                link_header = response.headers.get("Link", "")
                url = None
                if 'rel="next"' in link_header:
                    for part in link_header.split(","):
                        if 'rel="next"' in part:
                            url = part.split(";")[0].strip().strip("<>")
                    if url:
                        print("  → Fetching next page...")

            time.sleep(0.3)

        print(f"RESULT: {order_count} orders, ${total:.2f} total")

    # Also check order count directly from customer object
    print(f"\n{'='*50}")
    print(f"Customer orders_count field: {customer.get('orders_count', 'N/A')}")
    print(f"Customer total_spent field: {customer.get('total_spent', 'N/A')}")


if __name__ == "__main__":
    main()
