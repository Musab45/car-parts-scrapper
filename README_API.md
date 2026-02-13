# Auto Parts Scraper API

FastAPI application for scraping car parts data from autodoc.parts.

## Installation

```bash
pip install -r requirements.txt
```

## Running the API

```bash
python app.py
```

Or using uvicorn directly:

```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at: `http://localhost:8000`

## API Endpoints

### 1. Root Endpoint
```
GET /
```
Returns API information and available endpoints.

### 2. Health Check
```
GET /health
```
Check if the API and browser are running properly.

### 3. Scrape Product (POST)
```
POST /scrape
Content-Type: application/json

{
  "barcode": "34356790304 SK"
}
```

### 4. Scrape Product (GET)
```
GET /scrape/{barcode}
```
Example: `GET /scrape/34356790304%20SK`

## Response Format

### Success Response
```json
{
  "success": true,
  "barcode": "34356790304 SK",
  "data": {
    "barcode": "34356790304 SK",
    "product_url": "https://www.autodoc.parts/...",
    "product_name": "Brake Pad Set, disc brake",
    "price": "€25.99",
    "discount_percentage": "-15%",
    "vat_percentage": "19%",
    "image_urls": [
      "https://cdn.autodoc.parts/images/product/image1.jpg",
      "https://cdn.autodoc.parts/images/product/image2.jpg",
      "https://cdn.autodoc.parts/images/product/image3.jpg"
    ],
    "specifications": {
      "fitting_position": "Front Axle",
      "brake_type": "Disc Brake",
      "length_mm": "155.1",
      "width_mm": "68.3",
      "thickness_mm": "20.3",
      "manufacturer": "ZIMMERMANN",
      "ean_number": "4055359260304",
      "condition": "New"
    },
    "description_fields": {
      "Fitting Position": "Front Axle",
      "Brake Type": "Disc Brake",
      "Length [mm]": "155.1",
      "Width [mm]": "68.3",
      "Thickness [mm]": "20.3",
      "Manufacturer": "ZIMMERMANN",
      "EAN number": "4055359260304",
      "Condition": "New"
    }
  }
}
```

### Error Response
```json
{
  "success": false,
  "barcode": "INVALID123",
  "data": null,
  "error": "No product found for this barcode"
}
```

## Testing the API

### Using curl (POST)
```bash
curl -X POST http://localhost:8000/scrape \
  -H "Content-Type: application/json" \
  -d '{"barcode": "34356790304 SK"}'
```

### Using curl (GET)
```bash
curl http://localhost:8000/scrape/34356790304%20SK
```

### Using Python requests
```python
import requests

# POST method
response = requests.post(
    "http://localhost:8000/scrape",
    json={"barcode": "34356790304 SK"}
)
print(response.json())

# GET method
response = requests.get("http://localhost:8000/scrape/34356790304%20SK")
print(response.json())
```

### Using Browser
Open http://localhost:8000/docs for interactive API documentation (Swagger UI)

## Features

- ✅ FastAPI framework with async support
- ✅ Browser lifecycle management (starts on app startup, closes on shutdown)
- ✅ Headless Chrome for production use
- ✅ Cookie consent handling
- ✅ Structured JSON responses with product images URLs
- ✅ Both POST and GET endpoints
- ✅ Proper error handling
- ✅ Interactive API documentation (Swagger UI)
- ✅ Pydantic models for request/response validation

## Notes

- The browser runs in headless mode for better performance
- High-resolution image URLs (2x) are extracted and returned in the response
- Images are not downloaded to disk in API mode (only URLs are returned)
- The browser instance is reused across requests for better performance
- Cookie consent is handled once per session
