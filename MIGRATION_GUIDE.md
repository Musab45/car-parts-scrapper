# Migration Guide — Pushed (`08dedcd`) → Current

This document describes **every breaking change** between the last Git push and the current `app.py`.
If you have a system that consumes this API, follow each section carefully.

---

## 1. Response JSON Structure (BREAKING)

The `data` field inside every `ProductResponse` has been completely restructured from **flat** to **nested**, and new fields have been added.

### 1.1 Autodoc Scraper — `data` shape

#### BEFORE (pushed)
```jsonc
{
  "success": true,
  "barcode": "34116860912",
  "scraper": "autodoc",
  "data": {
    "product_url": "https://www.autodoc.co.uk/...",
    "product_name": "TRUCKTEC AUTOMOTIVE 08.25.056 ...",
    "price": "£55. 99",
    "discount_percentage": "-15%",
    "vat_percentage": "20%",
    "images_folder": "images/34116860912",
    "images_downloaded": 2,
    "specifications": {
      "outer_diameter": "300 mm",
      "weight_kg": "6.8"
    }
  }
}
```

#### AFTER (current)
```jsonc
{
  "success": true,
  "barcode": "34116860912",
  "scraper": "autodoc",
  "data": {
    "product": {                          // was flat keys
      "name": "TRUCKTEC AUTOMOTIVE ...",  // was "product_name"
      "url": "https://www.autodoc.co.uk/..."  // was "product_url"
    },
    "pricing": {                          // was flat keys
      "price": "£55. 99",
      "discount_percentage": "-15%",
      "vat_percentage": "20%"
    },
    "media": {                            // was flat keys
      "images_folder": "images/34116860912",
      "images_downloaded": 2
    },
    "specifications": {                   // unchanged structure
      "outer_diameter": "300 mm",
      "weight_kg": "6.8"
    },
    "compatibility": {                    // 🆕 NEW — scraped from .summary-table
      "car_models": ["BMW F20", "BMW F11"],
      "engines": ["125i", "520i"],
      "year_of_manufacture": "2010-2021",
      "manufacturer_article_number": "316T0378",
      "oe_part_numbers": ["1153 8 635 689"]
    },
    "oe_numbers": [                       // 🆕 NEW — scraped from #oem accordion
      {
        "number": "1153 8 635 689",
        "brand": "BMW / BMW (BRILLIANCE)",
        "url": "https://www.autodoc.co.uk/..."
      }
    ]
  }
}
```

#### Key renames / moves
| Old key (flat) | New key (nested) |
|---|---|
| `data.product_name` | `data.product.name` |
| `data.product_url` | `data.product.url` |
| `data.price` | `data.pricing.price` |
| `data.discount_percentage` | `data.pricing.discount_percentage` |
| `data.vat_percentage` | `data.pricing.vat_percentage` |
| `data.images_folder` | `data.media.images_folder` |
| `data.images_downloaded` | `data.media.images_downloaded` |
| `data.specifications` | `data.specifications` *(unchanged)* |
| *(did not exist)* | `data.compatibility` 🆕 |
| *(did not exist)* | `data.oe_numbers` 🆕 |

#### New fields explained
- **`compatibility`** — dict scraped from the `.summary-table` on the product page. Known array fields (`car_models`, `engines`, `oe_part_numbers`) are split into arrays; everything else is a string.
- **`oe_numbers`** — array of objects scraped from the `#oem` accordion. Each has `number`, `brand`, and `url`.

---

### 1.2 RealOEM Scraper — `data` shape

#### BEFORE (pushed)
```jsonc
{
  "success": true,
  "barcode": "17217593856",
  "scraper": "realoem",
  "data": {
    "part_number": "17 21 7 593 856",
    "description": "Coolant Hose",
    "from_date": "2012/07",
    "to_date": "",
    "weight": "0.15",
    "price": "$12.34",
    "vehicle_count": 3,
    "first_vehicle_tags": "F20 LCI"
  }
}
```

#### AFTER (current)
```jsonc
{
  "success": true,
  "barcode": "17217593856",
  "scraper": "realoem",
  "data": {
    "product": {                                // was flat keys
      "part_number": "17 21 7 593 856",         // was "part_number"
      "description": "Coolant Hose"             // was "description"
    },
    "pricing": {                                // was flat key
      "price": "$12.34"                         // was "price"
    },
    "details": {                                // was flat keys
      "from_date": "2012/07",                   // was "from_date"
      "to_date": null,                          // was "" (now null when empty)
      "weight": "0.15"                          // was "weight"
    },
    "compatibility": {                          // was flat keys
      "vehicle_count": 3,                       // was "vehicle_count"
      "first_vehicle_tags": "F20 LCI"           // was "first_vehicle_tags"
    }
  }
}
```

#### Key renames / moves
| Old key (flat) | New key (nested) |
|---|---|
| `data.part_number` | `data.product.part_number` |
| `data.description` | `data.product.description` |
| `data.price` | `data.pricing.price` |
| `data.from_date` | `data.details.from_date` |
| `data.to_date` | `data.details.to_date` |
| `data.weight` | `data.details.weight` |
| `data.vehicle_count` | `data.compatibility.vehicle_count` |
| `data.first_vehicle_tags` | `data.compatibility.first_vehicle_tags` |

#### Null vs empty string change
- **Before:** missing values returned `""` (empty string)
- **After:** missing values return `null`

---

## 2. `/health` Endpoint Response (BREAKING)

#### BEFORE
```json
{
  "status": "healthy",
  "browser_active": true
}
```

#### AFTER
```json
{
  "status": "healthy",
  "pool": {
    "total": 1,
    "busy": 0,
    "idle": 1,
    "permanent": 1,
    "temp": 0
  }
}
```

| Old field | Replacement |
|---|---|
| `browser_active` (bool) | `pool.total > 0` or `pool.idle > 0` |

The `pool` object tells you:
- **`total`** — how many Chrome instances exist right now (max 3)
- **`busy`** — currently handling a scrape request
- **`idle`** — available for a new request
- **`permanent`** — always-on instance (always 1)
- **`temp`** — on-demand instances (0–2), auto-killed after 5 min idle

---

## 3. New HTTP Status Code: `503` (NEW)

#### BEFORE
Requests queued silently; only one could run at a time; the rest timed out.

#### AFTER
If all 3 browser instances are busy and none becomes free within 30 seconds:

```
HTTP 503 Service Unavailable
{ "detail": "All scrapers busy, please retry in a moment" }
```

**Action required:** your client should retry on 503 with backoff (e.g. 2–5 s).

---

## 4. Concurrency Model (BEHAVIORAL)

| Aspect | Before | After |
|---|---|---|
| **Chrome instances** | 1 global, created on first request | Pool: 1 permanent (always warm) + up to 2 temp (spawned on demand) |
| **Max concurrent scrapes** | 1 | 3 |
| **Request queuing** | Blocked indefinitely | `pool.acquire(timeout=30)` → 503 if no instance free |
| **Timeout enforcement** | None | Watchdog timer kills the driver on timeout (120 s autodoc / 300 s realoem) |
| **Process cleanup on shutdown** | None | `atexit` + `SIGTERM` handler → `pool.shutdown()` → force-kill Chrome PIDs |
| **Process cleanup on startup** | None | `_kill_orphaned_chromedrivers()` kills stale `chromedriver` + `Google Chrome for Testing` processes |
| **Idle temp instance cleanup** | N/A | Background reaper kills temp instances idle > 5 min |

---

## 5. Pydantic Models Added (NON-BREAKING — schema only)

These exist in the code for documentation/validation but don't change the wire format beyond what Section 1 describes:

```
AutodocProduct, AutodocPricing, AutodocMedia, AutodocOENumber, AutodocData
RealOEMProduct, RealOEMPricing, RealOEMDetails, RealOEMCompatibility, RealOEMData
```

---

## 6. Implementation Checklist for Consuming Systems

### Database / ORM
- [ ] Add columns/fields for `compatibility` (dict/JSON) and `oe_numbers` (array of objects) if using Autodoc
- [ ] Update column mappings: `product_name` → `product.name`, `product_url` → `product.url`, etc.
- [ ] Handle `null` instead of `""` for empty RealOEM fields (`from_date`, `to_date`, `weight`, `price`)

### API Client Code
- [ ] Update JSON parsing to navigate nested structure (`data.product.name` instead of `data.product_name`)
- [ ] Add retry logic for HTTP 503 (all scrapers busy)
- [ ] Update health check parsing: `response.pool.idle > 0` instead of `response.browser_active`

### Monitoring / Alerts
- [ ] Use `/health` → `pool.busy` / `pool.total` for load monitoring
- [ ] Alert on `pool.idle == 0` (all instances busy)
- [ ] Log/alert on 503 responses (capacity limit reached)

### Image Handling
- [ ] Update image path reference: `data.media.images_folder` instead of `data.images_folder`
- [ ] Update image count reference: `data.media.images_downloaded` instead of `data.images_downloaded`

### Example: adapting a Python client

```python
# BEFORE
name  = response["data"]["product_name"]
price = response["data"]["price"]
imgs  = response["data"]["images_downloaded"]

# AFTER
name  = response["data"]["product"]["name"]
price = response["data"]["pricing"]["price"]
imgs  = response["data"]["media"]["images_downloaded"]

# NEW fields
compat   = response["data"]["compatibility"]     # dict
oe_nums  = response["data"]["oe_numbers"]         # list[dict]
```

### Example: adapting a JavaScript client

```javascript
// BEFORE
const name  = data.product_name;
const price = data.price;

// AFTER
const name  = data.product.name;
const price = data.pricing.price;

// NEW fields
const compat  = data.compatibility;   // { car_models: [...], engines: [...], ... }
const oeNums  = data.oe_numbers;      // [{ number, brand, url }, ...]
```
