# Image API Guide for Frontend

## Image Endpoint

```
GET /images/{barcode}/{image_number}
```

## How It Works

1. After scraping a product with **AUTODOC**, images are downloaded locally
2. Images are saved sequentially: `image_1.jpg`, `image_2.jpg`, `image_3.jpg`, etc.
3. Use the endpoint to retrieve images by barcode and number

## Usage Examples

### Get First Image
```
GET http://localhost:8000/images/34116860912/1
```

### Get Second Image
```
GET http://localhost:8000/images/34116860912/2
```

### Get Third Image
```
GET http://localhost:8000/images/34116860912/3
```

## Response

- **Success**: Returns the image file (JPEG, PNG, WEBP)
- **Not Found**: HTTP 404 with `{"detail": "Image not found"}`

## Integration Examples

### HTML
```html
<img src="http://localhost:8000/images/34116860912/1" alt="Product Image 1">
<img src="http://localhost:8000/images/34116860912/2" alt="Product Image 2">
<img src="http://localhost:8000/images/34116860912/3" alt="Product Image 3">
```

### React/JavaScript
```javascript
const barcode = "34116860912";
const imageCount = 3; // Get from API response: data.images_downloaded

// Display all images
{Array.from({ length: imageCount }, (_, i) => (
  <img 
    key={i}
    src={`http://localhost:8000/images/${barcode}/${i + 1}`}
    alt={`Product ${i + 1}`}
    onError={(e) => e.target.style.display = 'none'}
  />
))}
```

### Vue.js
```vue
<template>
  <div class="product-images">
    <img 
      v-for="n in imageCount" 
      :key="n"
      :src="`http://localhost:8000/images/${barcode}/${n}`"
      :alt="`Product Image ${n}`"
      @error="hideImage"
    />
  </div>
</template>

<script>
export default {
  data() {
    return {
      barcode: '34116860912',
      imageCount: 3 // From API response
    }
  }
}
</script>
```

### Fetch API (JavaScript)
```javascript
async function getProductWithImages(barcode) {
  // 1. Scrape product data
  const response = await fetch('http://localhost:8000/scrape', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ barcode, scraper: 'autodoc' })
  });
  
  const result = await response.json();
  
  if (!result.success) {
    throw new Error(result.error);
  }
  
  // 2. Build image URLs
  const imageCount = result.data.images_downloaded;
  const imageUrls = Array.from(
    { length: imageCount }, 
    (_, i) => `http://localhost:8000/images/${barcode}/${i + 1}`
  );
  
  return {
    ...result.data,
    imageUrls
  };
}

// Usage
getProductWithImages('34116860912').then(product => {
  console.log('Product:', product.product_name);
  console.log('Images:', product.imageUrls);
  // Display images in your UI
});
```

### Python (Requests)
```python
import requests

def get_product_images(barcode, scraper='autodoc'):
    # 1. Scrape product
    response = requests.post(
        'http://localhost:8000/scrape',
        json={'barcode': barcode, 'scraper': scraper}
    )
    data = response.json()
    
    if not data['success']:
        raise Exception(data['error'])
    
    # 2. Build image URLs
    image_count = data['data']['images_downloaded']
    image_urls = [
        f"http://localhost:8000/images/{barcode}/{i}"
        for i in range(1, image_count + 1)
    ]
    
    return image_urls

# Download images
images = get_product_images('34116860912')
for idx, url in enumerate(images, 1):
    img_response = requests.get(url)
    with open(f'downloaded_image_{idx}.jpg', 'wb') as f:
        f.write(img_response.content)
```

## Complete Workflow

```javascript
// Step 1: Scrape product data
const scrapeProduct = async (barcode) => {
  const response = await fetch('http://localhost:8000/scrape', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ 
      barcode: barcode,
      scraper: 'autodoc' 
    })
  });
  
  return await response.json();
};

// Step 2: Display product with images
const displayProduct = async (barcode) => {
  const result = await scrapeProduct(barcode);
  
  if (!result.success) {
    console.error('Scraping failed:', result.error);
    return;
  }
  
  const { product_name, price, images_downloaded } = result.data;
  
  console.log('Product:', product_name);
  console.log('Price:', price);
  console.log('Images available:', images_downloaded);
  
  // Build image gallery
  const gallery = document.getElementById('gallery');
  for (let i = 1; i <= images_downloaded; i++) {
    const img = document.createElement('img');
    img.src = `http://localhost:8000/images/${barcode}/${i}`;
    img.alt = `${product_name} - Image ${i}`;
    img.onerror = () => img.remove(); // Remove if 404
    gallery.appendChild(img);
  }
};

// Usage
displayProduct('34116860912');
```

## Important Notes

### ⚠️ Image Availability
- **AUTODOC only**: Images are downloaded during scraping
- **REALOEM**: No images available (returns 0 for `images_downloaded`)
- Always check `images_downloaded` count from API response

### ⚠️ Error Handling
```javascript
// Always handle 404 errors
<img 
  src={`http://localhost:8000/images/${barcode}/${imageNum}`}
  onError={(e) => {
    e.target.style.display = 'none'; // Hide broken images
    // or use a placeholder
    e.target.src = '/placeholder.png';
  }}
/>
```

### ⚠️ Image Count
```javascript
// Get image count from scrape response
const response = await scrapeProduct(barcode);
const imageCount = response.data.images_downloaded; // Use this!

// Don't hardcode or guess the count
```

### ⚠️ Barcode Sanitization
The API automatically handles special characters in barcodes:
- Spaces → `_`
- Forward slash → `-`
- Backslash → `-`

Example: Barcode `"ABC 123/456"` → folder `"ABC_123-456"`

You don't need to sanitize - just pass the original barcode.

## CORS Configuration (if needed)

If calling from a different domain, add CORS to `app.py`:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Or specify your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## Production Considerations

1. **Base URL**: Replace `http://localhost:8000` with your production URL
2. **CDN**: Consider uploading images to a CDN for better performance
3. **Caching**: Images are static - enable browser caching
4. **Lazy Loading**: Use lazy loading for better page performance
```html
<img loading="lazy" src="..." />
```
5. **Image Optimization**: Consider compressing images before serving

## Quick Reference

| Field | Where | Description |
|-------|-------|-------------|
| `images_downloaded` | Scrape API response | Number of images available (0 for RealOEM) |
| `images_folder` | Scrape API response | Local folder path (for reference only) |
| Image numbers | Image URL | Sequential: 1, 2, 3, ... up to `images_downloaded` |
| Supported formats | Automatic | .jpg, .jpeg, .png, .webp |

## Testing

```bash
# 1. Scrape a product (AUTODOC)
curl -X POST http://localhost:8000/scrape \
  -H "Content-Type: application/json" \
  -d '{"barcode": "34116860912", "scraper": "autodoc"}'

# 2. Get first image
curl http://localhost:8000/images/34116860912/1 --output image1.jpg

# 3. Check in browser
open http://localhost:8000/images/34116860912/1
```
