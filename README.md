# Shopify work samples

Selected code from a production Shopify store I build and run: a designer's catalog of house plans, sold direct and through national plan marketplaces. The pieces here are the ones a Shopify developer or store owner can read and reuse. They have been lightly edited so they carry no client data: no store names, domains, account numbers, contract terms or customer information.

This is a curated sample, not the whole toolkit. The sales software behind the store (it reads years of order and marketplace emails into one ledger), the desktop apps and the store's full theme stay private.

Case study with results: [atlasdigital.design/work/intertwine/](https://atlasdigital.design/work/intertwine/)

## What is here

| Path | What it is |
|---|---|
| `shopify-theme/snippets/product-schema-body.liquid` | Product JSON-LD for a product type with no schema.org vocabulary. Plan specs come from metafields and go out as numbers (width and depth in feet with UN/CEFACT unit codes), one `Offer` per variant with shipping and return terms, and a stable `@id` graph shared with the Organization and collection markup. |
| `shopify-theme/snippets/collection-schema.liquid` | `CollectionPage` + `ItemList` + `BreadcrumbList`. The item list is suppressed on paginated views, so the markup never lists products the page does not show. |
| `shopify-theme/templates/collection.pinterest-feed.liquid` | A replacement Pinterest catalog feed in Liquid: RSS 2.0 with the Google Merchant `g:` namespace, one row per product at its entry price, tagged links, and a phased cutover plan in the header. Written after the native integration was found advertising variant prices well above the entry price. |
| `tools/pinterest/pin_compose.py` | Composes four pin layouts per product from renders and floor plans with Pillow. Names are measured and shrunk or wrapped to fit, and captions come from the file actually used. |
| `tools/pricing/sale_price.py` | A sitewide sale as a real price change through the Admin GraphQL API: capture a rollback file first, dry run, apply on a typed confirmation, re-read and diff every write, restore verbatim from the file. |
| `tools/split-engine/` | The allocation core of a revenue-split app: `Decimal` money, the fee and every share allocated from the gross in one largest-remainder pass so the parts always reconcile to the cent, and weights validated at load. Tests run against an invented example contract. The app around it is not included. |

## How the work is checked

Each change shipped on the store was proposed before it was made, verified against the live site after it went out, and logged with a rollback. Comments in the code record why a rule exists, usually because something failed without it. Examples in these files: the Liquid loop that silently stops at 50 items without `paginate`, the image URL whose raw `&` breaks a strict XML parse while every browser renders it fine, and the metafield compared as an object instead of its `.value`.

## Running the tests

```bash
cd tools/split-engine
python3 -m unittest test_splits -v
```

## Contact

Jeff Combs, Atlas Digital Design. [atlasdigital.design](https://atlasdigital.design) · jeff@atlasdigital.design
