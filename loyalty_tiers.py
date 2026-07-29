import os
import requests
import time
from datetime import datetime, timedelta, timezone

STORE = os.environ["SHOPIFY_STORE"]
CLIENT_SECRET = os.environ["SHOPIFY_CLIENT_SECRET"]

GRAPHQL_URL = f"https://{STORE}.myshopify.com/admin/api/2026-07/graphql.json"
REST_URL = f"https://{STORE}.myshopify.com/admin/api/2026-07"
TEST_EMAIL = "kiwipal2@gmail.com"


def get_headers():
    return {
        "X-Shopify-Access-Token": CLIENT_SECRET,
        "Content-Type": "application/json"
    }


def main():
    # Find customer via REST
    response = requests.get(
        f"{REST_URL}/customers/search.json?query=email:{TEST_EMAIL}",
        headers=get_headers()
    )
    customer = response.json().get("customers", [])[0]
    customer_id = customer["id"]
    gql_id = f"gid://shopify/Customer/{customer_id}"
    print(f"Found: {customer['first_name']} {customer['last_name']}")

    twelve_months_ago = datetime.now(timezone.utc) - timedelta(days=365)

    total = 0.0
    order_count = 0
    has_next_page = True
    cursor = None
    page = 0

    while has_next_page:
        after_clause = f', after: "{cursor}"' if cursor else ""
        
        # No date filter — fetch ALL orders and filter by processedAt in Python
        query = f"""
        {{
          customer(id: "{gql_id}") {{
            orders(first: 250{after_clause}) {{
              pageInfo {{ hasNextPage endCursor }}
              edges {{
                node {{
                  name
                  createdAt
                  processedAt
                  displayFinancialStatus
                  totalPriceSet {{ shopMoney {{ amount }} }}
                }}
              }}
            }}
          }}
        }}
        """

        result = requests.post(GRAPHQL_URL, headers=get_headers(), json={"query": query}).json()

        if "errors" in result:
            print(f"GraphQL errors: {result['errors']}")
            return

        orders_data = result["data"]["customer"]["orders"]
        edges = orders_data["edges"]
        page_info = orders_data["pageInfo"]
        page += 1

        print(f"\nPage {page}: {len(edges)} orders (hasNextPage: {page_info['hasNextPage']})")

        stop = False
        for edge in edges:
            order = edge["node"]
            status = order["displayFinancialStatus"]
            amount = float(order["totalPriceSet"]["shopMoney"]["amount"])
            name = order["name"]
            created_at = datetime.fromisoformat(order["createdAt"].replace("Z", "+00:00"))
            processed_at = datetime.fromisoformat(order["processedAt"].replace("Z", "+00:00"))

            # Check both dates
            within_12m_created = created_at >= twelve_months_ago
            within_12m_processed = processed_at >= twelve_months_ago

            if not within_12m_created and not within_12m_processed:
                print(f"  STOP — {name} both dates older than 12 months (created: {created_at.strftime('%Y-%m-%d')}, processed: {processed_at.strftime('%Y-%m-%d')})")
                stop = True
                break

            if status in ["PAID", "PARTIALLY_REFUNDED"]:
                if within_12m_processed:
                    total += amount
                    order_count += 1
                    print(f"  ✓ {name} created:{created_at.strftime('%Y-%m-%d')} processed:{processed_at.strftime('%Y-%m-%d')}: ${amount}")
                else:
                    print(f"  ~ {name} created in range but processedAt too old: {processed_at.strftime('%Y-%m-%d')}")

        if stop:
            break

        has_next_page = page_info["hasNextPage"]
        cursor = page_info["endCursor"]
        time.sleep(0.3)

    print(f"\n{'='*50}")
    print(f"Total orders (12 months by processedAt): {order_count}")
    print(f"Total spend: ${total:.2f}")
    if total >= 1500:
        print("→ Tier: PLATINUM ✓")
    elif total >= 1000:
        print("→ Tier: GOLD")
    else:
        print("→ Tier: SILVER")


if __name__ == "__main__":
    main()
