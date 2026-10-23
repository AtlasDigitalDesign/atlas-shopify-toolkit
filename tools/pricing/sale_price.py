#!/usr/bin/env python3
"""
Sitewide sale as a REAL price change: feeds and ads see it, discount codes don't.
Written for a Labor Day 2026 sale (16% off, 64 products, 327 variants).

Usage
  export SHOPIFY_STORE=your-store.myshopify.com
  export SHOPIFY_TOKEN=shpat_...          # Admin API token with write_products
  python3 sale_price.py capture     # writes price_rollback_<date>.csv, no writes
  python3 sale_price.py dryrun      # prints before/after table, no writes
  python3 sale_price.py apply       # sets price = compare_at * 0.84 (asks for 'go')
  python3 sale_price.py restore price_rollback_<date>.csv   # verbatim restore

Rules baked in
  - Only product_type 'Plan Set', status active. Study Sets and anything without a
    compare-at price are skipped.
  - compare_at_price is never touched. It is the list price, and the sale is shown
    against it.
  - Restore is verbatim from the CSV, never recomputed, so deliberate catalog fixes made
    between capture and restore are not undone by accident.
  - Every write is followed by a re-read and a diff against what was expected.

The same operations run through the Shopify MCP in a Claude session:
  read  : products(query:"product_type:'Plan Set' AND status:active") { variants { id price compareAtPrice } }
  write : productVariantsBulkUpdate(productId, variants:[{id, price}])
"""
import csv, json, os, sys, time, urllib.request, datetime

STORE = os.environ.get("SHOPIFY_STORE")
TOKEN = os.environ.get("SHOPIFY_TOKEN")
API = f"https://{STORE}/admin/api/2025-07/graphql.json"
FACTOR = float(os.environ.get("SALE_FACTOR", "0.84"))   # 0.84 = 16% off

def gql(query, variables=None):
    req = urllib.request.Request(API, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        out = json.load(r)
    if "errors" in out:
        raise SystemExit(out["errors"])
    return out["data"]

def read_catalog():
    rows, after = [], None
    while True:
        d = gql("""query($after:String){ products(first:50, after:$after, query:"product_type:'Plan Set' AND status:active"){
                   nodes{ id handle title variants(first:50){ nodes{ id title price compareAtPrice } } }
                   pageInfo{ hasNextPage endCursor } } }""", {"after": after})
        for p in d["products"]["nodes"]:
            for v in p["variants"]["nodes"]:
                rows.append({"product_id": p["id"], "handle": p["handle"], "variant_id": v["id"],
                             "variant": v["title"], "price": v["price"], "compare_at": v["compareAtPrice"] or ""})
        pi = d["products"]["pageInfo"]
        if not pi["hasNextPage"]: break
        after = pi["endCursor"]
    return rows

def capture(path=None):
    rows = read_catalog()
    path = path or f"price_rollback_{datetime.date.today().isoformat()}.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    print(f"captured {len(rows)} variants across {len({r['product_id'] for r in rows})} products -> {path}")
    return rows, path

def plan(rows):
    out = []
    for r in rows:
        if not r["compare_at"]: continue
        new = round(float(r["compare_at"]) * FACTOR, 2)
        out.append((r, new))
    return out

def show(planned):
    print(f"{'handle':40} {'variant':52} {'now':>10} {'sale':>10} {'list':>10}")
    for r, new in planned:
        print(f"{r['handle'][:40]:40} {r['variant'][:52]:52} {float(r['price']):>10.2f} {new:>10.2f} {float(r['compare_at']):>10.2f}")
    print(f"\n{len(planned)} variants would change.")

def write(updates):
    # updates: {product_id: [(variant_id, price), ...]}
    for pid, vs in updates.items():
        d = gql("""mutation($pid:ID!,$v:[ProductVariantsBulkInput!]!){
                   productVariantsBulkUpdate(productId:$pid, variants:$v){ userErrors{ field message } } }""",
                {"pid": pid, "v": [{"id": vid, "price": f"{price:.2f}"} for vid, price in vs]})
        errs = d["productVariantsBulkUpdate"]["userErrors"]
        if errs: print("ERROR", pid, errs)
        time.sleep(0.4)

def verify(expected):
    live = {r["variant_id"]: float(r["price"]) for r in read_catalog()}
    bad = [(vid, exp, live.get(vid)) for vid, exp in expected.items() if abs(live.get(vid, -1) - exp) > 0.005]
    print("verify:", "OK, all match" if not bad else f"{len(bad)} MISMATCHES: {bad[:10]}")

if __name__ == "__main__":
    if not (STORE and TOKEN): raise SystemExit("set SHOPIFY_STORE and SHOPIFY_TOKEN")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dryrun"
    if cmd == "capture":
        capture()
    elif cmd == "dryrun":
        show(plan(read_catalog()))
    elif cmd == "apply":
        rows, path = capture()
        planned = plan(rows); show(planned)
        if input("\nType 'go' to apply the sale prices: ").strip() != "go": raise SystemExit("aborted")
        ups, exp = {}, {}
        for r, new in planned:
            ups.setdefault(r["product_id"], []).append((r["variant_id"], new)); exp[r["variant_id"]] = new
        write(ups); verify(exp)
    elif cmd == "restore":
        rows = list(csv.DictReader(open(sys.argv[2])))
        ups, exp = {}, {}
        for r in rows:
            ups.setdefault(r["product_id"], []).append((r["variant_id"], float(r["price"]))); exp[r["variant_id"]] = float(r["price"])
        if input(f"Type 'go' to restore {len(rows)} prices from {sys.argv[2]}: ").strip() != "go": raise SystemExit("aborted")
        write(ups); verify(exp)
