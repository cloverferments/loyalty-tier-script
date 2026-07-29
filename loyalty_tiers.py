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
    customers = response.json().get("customers", [])
    if not customers:
        print("Customer not found")
        return

    customer = customers[0]
    customer_id = customer["id"]
    print(f"Found: {customer['first_name']} {customer['last_name']} (ID: {customer_id})")

    twelve_months_ago = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    gql_id = f"gid://shopify/Customer/{customer_id}"
    
    total = 0.0
    order_count = 0
    has_next_page = True
    cursor = None
    page = 0

    while has_next_page:
        after_clause = f', after: "{cursor}"' if cursor else ""
        query = f"""
        {{
          customer(id: "{gql_id}") {{
            orders(first: 250{after_clause}, query: "created_at:>={twelve_months_ago}") {{
              pageInfo {{ hasNextPage endCursor }}
              edges {{
                node {{
                  name
                  createdAt
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

        for edge in edges:
            order = edge["node"]
            status = order["displayFinancialStatus"]
            amount = float(order["totalPriceSet"]["shopMoney"]["amount"])
            name = order["name"]
            date = order["createdAt"][:10]

            if status in ["PAID", "PARTIALLY_REFUNDED"]:
                total += amount
                order_count += 1
                print(f"  ✓ {name} ({date}): ${amount} [{status}]")
            else:
                print(f"  ✗ SKIP {name} ({date}): ${amount} [{status}]")

        has_next_page = page_info["hasNextPage"]
        cursor = page_info["endCursor"]
        time.sleep(0.3)

    print(f"\n{'='*50}")
    print(f"Total orders: {order_count}")
    print(f"Total spend (12 months): ${total:.2f}")
    if total >= 1500:
        print("→ Tier: PLATINUM ✓")
    elif total >= 1000:
        print("→ Tier: GOLD")
    else:
        print("→ Tier: SILVER")


if __name__ == "__main__":
    main()
