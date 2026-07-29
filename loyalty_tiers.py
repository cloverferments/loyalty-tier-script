import os
import requests
from datetime import datetime, timedelta, timezone

STORE = os.environ["SHOPIFY_STORE"]
CLIENT_SECRET = os.environ["SHOPIFY_CLIENT_SECRET"]

GRAPHQL_URL = f"https://{STORE}.myshopify.com/admin/api/2026-07/graphql.json"
REST_URL = f"https://{STORE}.myshopify.com/admin/api/2026-07"
TEST_EMAIL = "kiwipal2@gmail.com"


def get_headers():
    return {"X-Shopify-Access-Token": CLIENT_SECRET, "Content-Type": "application/json"}


def main():
    # Get customer
    response = requests.get(f"{REST_URL}/customers/search.json?query=email:{TEST_EMAIL}", headers=get_headers())
    customer = response.json().get("customers", [])[0]
    customer_id = customer["id"]
    gql_id = f"gid://shopify/Customer/{customer_id}"
    print(f"Customer ID: {customer_id}")
    print(f"REST orders_count: {customer.get('orders_count')}")
    print(f"REST total_spent: {customer.get('total_spent')}")

    # Try GraphQL with no filters at all - just count
    query = f"""
    {{
      customer(id: "{gql_id}") {{
        numberOfOrders
        totalSpentV2 {{ amount currencyCode }}
        orders(first: 250) {{
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
        print(f"Errors: {result['errors']}")
        return
    
    cdata = result["data"]["customer"]
    print(f"\nGraphQL numberOfOrders: {cdata.get('numberOfOrders')}")
    print(f"GraphQL totalSpent: {cdata.get('totalSpentV2')}")
    
    orders = cdata["orders"]["edges"]
    page_info = cdata["orders"]["pageInfo"]
    print(f"GraphQL orders returned: {len(orders)}")
    print(f"hasNextPage: {page_info['hasNextPage']}")
    
    print("\nAll orders returned:")
    for edge in orders:
        o = edge["node"]
        print(f"  {o['name']} created:{o['createdAt'][:10]} processed:{o['processedAt'][:10]} status:{o['displayFinancialStatus']} total:${o['totalPriceSet']['shopMoney']['amount']}")


if __name__ == "__main__":
    main()
