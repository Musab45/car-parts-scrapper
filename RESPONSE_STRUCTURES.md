# API Response Structures for Odoo Integration

## Base Response Wrapper

All endpoints return this structure:

```json
{
  "success": true/false,
  "barcode": "34116860912",
  "scraper": "autodoc" or "realoem",
  "data": { ... },  // Scraper-specific data (null if success=false)
  "error": "error message"  // Only present if success=false
}
```

---

## 1. AUTODOC Scraper Response

### Endpoint
```
POST /scrape
{
  "barcode": "34116860912",
  "scraper": "autodoc"
}

GET /scrape/34116860912?scraper=autodoc
```

### Success Response Structure
```json
{
  "success": true,
  "barcode": "34116860912",
  "scraper": "autodoc",
  "data": {
    "product_url": "https://www.autodoc.parts/product/...",
    "product_name": "Brake Disc BOSCH 0 986 478 861",
    "price": "€ 45.50",
    "discount_percentage": "-15%" or "N/A",
    "vat_percentage": "19%" or "N/A",
    "images_folder": "images/34116860912",
    "images_downloaded": 3,
    "specifications": {
      "outer_diameter": "300 mm",
      "brake_disc_thickness": "22 mm",
      "minimum_thickness": "20 mm",
      "height": "41 mm",
      "centering_diameter": "68 mm",
      "number_of_holes": "5",
      "surface": "coated",
      "brake_disc_type": "ventilated",
      "supplementary_article/info": "with bolts/screws",
      // ... more dynamic specifications based on product
    }
  },
  "error": null
}
```

### Key Fields Explained (AUTODOC)
| Field | Type | Description |
|-------|------|-------------|
| `product_url` | string | Full URL to the product page |
| `product_name` | string or null | Product title/name |
| `price` | string or null | Price with currency symbol |
| `discount_percentage` | string | Discount % or "N/A" |
| `vat_percentage` | string | VAT % or "N/A" |
| `images_folder` | string | Local folder where images are saved |
| `images_downloaded` | integer | Count of successfully downloaded images |
| `specifications` | object | **Dynamic key-value pairs** of product specs |

**Important Notes:**
- `specifications` object is **dynamic** - keys vary by product type
- Common spec keys: `outer_diameter`, `brake_disc_thickness`, `height`, `number_of_holes`, `surface`, etc.
- Images are downloaded locally to `images/{barcode}/` folder
- Spec keys are auto-generated from page (spaces replaced with `_`, lowercased)

### Error Response Structure (AUTODOC)
```json
{
  "success": false,
  "barcode": "34116860912",
  "scraper": "autodoc",
  "data": null,
  "error": "No product found for this barcode"
}
```

---

## 2. REALOEM Scraper Response

### Endpoint
```
POST /scrape
{
  "barcode": "34116860912",
  "scraper": "realoem"
}

GET /scrape/34116860912?scraper=realoem
```

### Success Response Structure
```json
{
  "success": true,
  "barcode": "34116860912",
  "scraper": "realoem",
  "data": {
    "part_number": "34116860912",
    "description": "Brake disc, ventilated",
    "from_date": "2013-09",
    "to_date": "2016-12",
    "weight": "8.5 kg",
    "price": "$125.00",
    "vehicle_count": 15,
    "first_vehicle_tags": "F30 320i N20 Sedan"
  },
  "error": null
}
```

### Key Fields Explained (REALOEM)
| Field | Type | Description |
|-------|------|-------------|
| `part_number` | string | Official BMW part number |
| `description` | string | Part description |
| `from_date` | string | Valid from date (format: YYYY-MM or "") |
| `to_date` | string | Valid to date (format: YYYY-MM or "") |
| `weight` | string | Weight with unit or "" |
| `price` | string | Price with currency or "" |
| `vehicle_count` | integer | Number of compatible vehicles |
| `first_vehicle_tags` | string | First vehicle model tags (e.g., "F30 320i N20 Sedan") or "N/A" |

**Important Notes:**
- Date fields may be empty strings `""`
- `first_vehicle_tags` extracted from first compatible vehicle
- No image download functionality for RealOEM
- Fields like `weight`, `price` may be empty `""` if not available

### Error Response Structure (REALOEM)
```json
{
  "success": false,
  "barcode": "34116860912",
  "scraper": "realoem",
  "data": null,
  "error": "TimeoutException: Message: timeout"
}
```

Or if part not found:
```json
{
  "success": false,
  "barcode": "INVALID123",
  "scraper": "realoem",
  "data": {
    "success": false,
    "error": "Part number not found"
  },
  "error": null
}
```

---

## Common Error Scenarios

### 1. Empty Barcode
```json
{
  "detail": "Barcode cannot be empty"
}
// HTTP Status: 400
```

### 2. Invalid Scraper
```json
{
  "detail": "Scraper must be 'autodoc' or 'realoem'"
}
// HTTP Status: 400
```

### 3. No Product Found (AUTODOC)
```json
{
  "success": false,
  "barcode": "INVALID123",
  "scraper": "autodoc",
  "data": null,
  "error": "No product found for this barcode"
}
```

### 4. Scraping Error
```json
{
  "success": false,
  "barcode": "34116860912",
  "scraper": "autodoc",
  "data": null,
  "error": "TimeoutException: Message: Element not found"
}
```

---

## Odoo Integration Recommendations

### 1. Product Model Mapping (AUTODOC)
```python
# Odoo Product Model Fields
{
    'name': data['product_name'],
    'list_price': parse_price(data['price']),  # Convert "€ 45.50" to 45.50
    'default_code': barcode,  # Internal reference
    'description': data['product_url'],
    'image_1920': download_image_from_folder(data['images_folder']),
    
    # Technical specifications (store as JSON or create separate fields)
    'x_outer_diameter': data['specifications'].get('outer_diameter'),
    'x_brake_disc_thickness': data['specifications'].get('brake_disc_thickness'),
    'x_surface': data['specifications'].get('surface'),
    # ... map other specs dynamically
}
```

### 2. Product Model Mapping (REALOEM)
```python
# Odoo Product Model Fields
{
    'name': data['description'],
    'default_code': data['part_number'],
    'list_price': parse_price(data['price']),  # Convert "$125.00" to 125.00
    'weight': parse_weight(data['weight']),  # Convert "8.5 kg" to 8.5
    
    # BMW-specific fields
    'x_bmw_from_date': data['from_date'],
    'x_bmw_to_date': data['to_date'],
    'x_vehicle_compatibility_count': data['vehicle_count'],
    'x_vehicle_tags': data['first_vehicle_tags'],
}
```

### 3. API Call Pattern (Python/Odoo)
```python
import requests

def scrape_part_data(barcode, scraper='autodoc'):
    url = "http://localhost:8000/scrape"
    payload = {
        "barcode": barcode,
        "scraper": scraper
    }
    
    response = requests.post(url, json=payload)
    response.raise_for_status()
    
    result = response.json()
    
    if not result['success']:
        raise Exception(result.get('error', 'Scraping failed'))
    
    return result['data']

# Usage in Odoo
try:
    data = scrape_part_data('34116860912', 'autodoc')
    product_vals = {
        'name': data['product_name'],
        'list_price': float(data['price'].replace('€', '').strip()),
        # ... map other fields
    }
    product = env['product.product'].create(product_vals)
except Exception as e:
    _logger.error(f"Scraping failed: {e}")
```

### 4. Handling Images (AUTODOC only)
Images are saved to local folder `images/{barcode}/`. You'll need to:
1. Access the scraper server's filesystem, OR
2. Modify the API to return base64-encoded images, OR
3. Upload images to a shared storage and return URLs

### 5. Dynamic Specifications Handling
Since AUTODOC specifications are dynamic, consider:
- **Option A**: Store as JSON field in Odoo: `x_specifications = fields.Json()`
- **Option B**: Create dynamic product attributes/variants
- **Option C**: Parse and map common fields, ignore unknown ones

---

## Testing Endpoints

### Test AUTODOC
```bash
curl -X POST http://localhost:8000/scrape \
  -H "Content-Type: application/json" \
  -d '{"barcode": "34116860912", "scraper": "autodoc"}'
```

### Test REALOEM
```bash
curl -X GET "http://localhost:8000/scrape/34116860912?scraper=realoem"
```

### Health Check
```bash
curl http://localhost:8000/health
```

---

## Important Considerations for Odoo

1. **API Availability**: Ensure the FastAPI server is always running and accessible from Odoo
2. **Timeout Handling**: Scraping can take 10-30 seconds; set appropriate timeouts in Odoo HTTP calls
3. **Error Handling**: Always check `success` field before processing `data`
4. **Image Storage**: Plan how to transfer images from scraper server to Odoo
5. **Rate Limiting**: Consider adding delays between requests to avoid detection
6. **Background Jobs**: Use Odoo queue jobs for scraping to avoid blocking UI
7. **Data Validation**: Validate prices, weights, dates before saving to Odoo
8. **Specifications Schema**: Decide how to handle dynamic AUTODOC specs in Odoo schema

