# Auto Parts Scraper API

A FastAPI-based web service that scrapes car parts data from multiple sources (Autodoc and RealOEM).

## Features

- ✅ **Multi-Scraper Support**: Choose between Autodoc (general parts) and RealOEM (BMW parts)
- ✅ **JSON API**: Clean REST API with JSON responses
- ✅ **Web Frontend**: Beautiful HTML interface for easy testing
- ✅ **Optimized Scraping**: Handles popups, cookies, and anti-bot measures
- ✅ **Error Handling**: Graceful error handling with detailed messages

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Make sure the requirements include:
```
fastapi
uvicorn
undetected-chromedriver
selenium
requests
openpyxl
```

## Running the API

Start the API server:
```bash
uvicorn app:app --reload
```

The API will be available at: `http://localhost:8000`

## API Endpoints

### 1. Root Endpoint
**GET** `/`

Returns API information and available endpoints.

```bash
curl http://localhost:8000/
```

### 2. Health Check
**GET** `/health`

Check if the API and browser are running.

```bash
curl http://localhost:8000/health
```

### 3. Scrape Data (POST)
**POST** `/scrape`

Scrape product data for a given barcode.

**Request Body:**
```json
{
  "barcode": "34116860912",
  "scraper": "realoem"
}
```

**Parameters:**
- `barcode` (string, required): The product barcode to search for
- `scraper` (string, optional): Scraper to use - `"autodoc"` or `"realoem"` (default: `"autodoc"`)

**Example with curl:**
```bash
curl -X POST http://localhost:8000/scrape \
  -H "Content-Type: application/json" \
  -d '{"barcode": "34116860912", "scraper": "realoem"}'
```

**Response (Success):**
```json
{
  "success": true,
  "barcode": "34116860912",
  "scraper": "realoem",
  "data": {
    "part_number": "34 11 6 860 912",
    "description": "Brake pad set",
    "from_date": "2010-01",
    "to_date": "2015-12",
    "weight": "1.5 kg",
    "price": "$45.00",
    "vehicle_count": 15,
    "first_vehicle_tags": "E90, E91, E92",
    "vehicles": [
      {
        "text": "BMW 3 Series E90 Sedan",
        "url": "https://www.realoem.com/..."
      }
    ]
  }
}
```

**Response (Error):**
```json
{
  "success": false,
  "barcode": "34116860912",
  "scraper": "realoem",
  "error": "Part not found on realoem.com"
}
```

### 4. Scrape Data (GET)
**GET** `/scrape/{barcode}`

Alternative GET method for scraping.

**Query Parameters:**
- `scraper` (string, optional): Scraper to use - `"autodoc"` or `"realoem"` (default: `"autodoc"`)

**Example:**
```bash
# RealOEM scraper
curl "http://localhost:8000/scrape/34116860912?scraper=realoem"

# Autodoc scraper (default)
curl "http://localhost:8000/scrape/34116860912"
```

## Using the Web Frontend

1. Open `frontend.html` in your browser
2. Enter a barcode
3. Select a scraper from the dropdown
4. Click "Scrape Data"
5. View the results

Or open it directly from VS Code using the Simple Browser:
```
Right-click frontend.html → Open with Live Server
```

## RealOEM Response Format

```json
{
  "part_number": "34 11 6 860 912",
  "description": "Brake pad set",
  "from_date": "2010-01",
  "to_date": "2015-12",
  "weight": "1.5 kg",
  "price": "$45.00",
  "vehicle_count": 15,
  "first_vehicle_tags": "E90, E91, E92",
  "vehicles": [
    {
      "text": "BMW 3 Series E90 Sedan",
      "url": "https://www.realoem.com/..."
    }
  ]
}
```

## Autodoc Response Format

```json
{
  "product_url": "https://www.autodoc.parts/...",
  "product_name": "Brake Pad Set",
  "price": "€45.00",
  "discount_percentage": "-20%",
  "vat_percentage": "19%",
  "images_folder": "images/34116860912",
  "images_downloaded": 3,
  "specifications": {
    "manufacturer": "Bosch",
    "width": "150mm",
    "height": "60mm"
  }
}
```

## Testing with Python

Use the provided test script:
```bash
python test_api.py
```

Or test manually with Python:
```python
import requests

response = requests.post("http://localhost:8000/scrape", json={
    "barcode": "34116860912",
    "scraper": "realoem"
})

print(response.json())
```

## Error Handling

The API handles various error scenarios:

1. **Invalid barcode**: Returns 400 Bad Request
2. **Invalid scraper**: Returns 400 Bad Request
3. **Part not found**: Returns success=false with error message
4. **Scraping errors**: Returns detailed error information
5. **Connection errors**: Logs errors and returns error response

## Notes

- The browser is initialized lazily on the first request
- Chrome driver runs in the background (can be configured for headless mode)
- Images are downloaded to the `images/` folder for Autodoc scraper
- RealOEM scraper extracts numeric-only barcodes automatically
- Popup killers and cookie handlers are built-in

## Troubleshooting

**Browser not starting:**
- Check Chrome/Chromium is installed
- Try running manually: `python -c "import undetected_chromedriver as uc; uc.Chrome()"`

**CORS errors in browser:**
- Make sure API is running on localhost:8000
- Check browser console for errors

**Cloudflare blocking:**
- The API includes Cloudflare bypass logic
- Increase timeout if needed in `_wait_for_cloudflare()`

## License

MIT
