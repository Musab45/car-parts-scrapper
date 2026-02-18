from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import os
import time
import re
import logging
import requests
from typing import Optional, Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global driver instance
driver = None
wait = None
autodoc_cookie_handled = False
realoem_cookie_handled = False

def _wait_for_cloudflare(max_wait: int = 60) -> bool:
    """Wait for Cloudflare challenge to complete"""
    start = time.time()
    while time.time() - start < max_wait:
        try:
            title = driver.title
        except Exception:
            time.sleep(2)
            continue
        if "Just a moment" in title or "Checking your browser" in title:
            elapsed = int(time.time() - start)
            logger.warning(f"⚠️  Cloudflare challenge active, waiting... ({elapsed}s)")
            time.sleep(3)
        else:
            logger.info("✅ Cloudflare challenge passed")
            return True
    logger.error("❌ Cloudflare challenge did not resolve within timeout")
    return False


def initialize_driver():
    """Initialize browser driver lazily — matches notebook setup exactly"""
    global driver, wait
    
    if driver is None:
        try:
            logger.info("🚀 Initializing Chrome driver...")
            options = uc.ChromeOptions()
            options.add_argument("--start-maximized")
            options.add_argument("--disable-blink-features=AutomationControlled")
            
            logger.info("⏳ Creating Chrome instance...")
            driver = uc.Chrome(options=options)
            logger.info("⏳ Setting up WebDriverWait...")
            wait = WebDriverWait(driver, 15)
            logger.info("✅ Chrome driver initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Chrome driver: {str(e)}")
            logger.exception(e)
            raise
    
    return driver, wait


app = FastAPI(
    title="Auto Parts Scraper API",
    description="API for scraping car parts data from autodoc.parts",
    version="1.0.0"
)


class BarcodeRequest(BaseModel):
    barcode: str
    scraper: str = "autodoc"  # Default to autodoc, options: "autodoc" or "realoem"


class ProductResponse(BaseModel):
    success: bool
    barcode: str
    scraper: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


def warmup_autodoc():
    """Warm-up browser for autodoc by visiting homepage"""
    global autodoc_cookie_handled
    if not autodoc_cookie_handled:
        logger.info("🌐 Warming up: navigating to autodoc.parts homepage...")
        driver.get("https://www.autodoc.co.uk/")
        _wait_for_cloudflare(max_wait=60)
        handle_cookies()
        logger.info("✅ Browser warmed up for autodoc")


def handle_cookies():
    """Handle cookie consent popup"""
    global autodoc_cookie_handled
    if not autodoc_cookie_handled:
        try:
            logger.info("🍪 Attempting to handle cookie consent...")
            cookie_btn = wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, 'button[data-cookies="allow_all_cookies"]')
                )
            )
            driver.execute_script("arguments[0].click();", cookie_btn)
            autodoc_cookie_handled = True
            logger.info("✅ Cookie consent accepted")
            time.sleep(1)
        except Exception as e:
            logger.warning(f"⚠️  No cookie popup found or already handled: {str(e)[:100]}")
            pass


def get_first_product_link(barcode: str) -> Optional[str]:
    """Get the first product link from search results"""
    try:
        logger.info(f"🔍 Waiting for search results to load...")
        time.sleep(2)
        
        listing_items = driver.find_elements(By.CSS_SELECTOR, ".listing-item__wrap")
        logger.info(f"📦 Found {len(listing_items)} listing items")
        
        if not listing_items:
            logger.warning(f"⚠️  No products found for barcode: {barcode}")
            # Try alternative selectors
            alt_items = driver.find_elements(By.CSS_SELECTOR, ".listing-item, [data-product-id]")
            logger.info(f"🔄 Alternative selector found {len(alt_items)} items")
            if not alt_items:
                logger.error(f"❌ No products found with any selector")
                return None
            listing_items = alt_items
        
        first_item = listing_items[0]
        logger.info(f"📍 Extracting link from first listing item...")
        
        try:
            title_link = first_item.find_element(By.CSS_SELECTOR, ".listing-item__name")
            logger.info(f"✅ Found title link using .listing-item__name")
        except Exception as e1:
            logger.warning(f"⚠️  Primary selector failed: {str(e1)[:50]}, trying alternative...")
            try:
                title_link = first_item.find_element(By.CSS_SELECTOR, "a.listing-item__name, [data-link]")
                logger.info(f"✅ Found title link using alternative selector")
            except Exception as e2:
                logger.error(f"❌ Alternative selector also failed: {str(e2)[:50]}")
                # Try to find any link in the item
                try:
                    title_link = first_item.find_element(By.TAG_NAME, "a")
                    logger.info(f"✅ Found link using generic 'a' tag")
                except Exception as e3:
                    logger.error(f"❌ No link found at all: {str(e3)[:50]}")
                    raise
        
        href = title_link.get_attribute("href")
        logger.info(f"🔗 Extracted href: {href}")
        
        if not href:
            href = title_link.get_attribute("data-link")
            logger.info(f"🔗 Using data-link instead: {href}")
        
        if href and "#" in href:
            href = href.split("#")[0]
            logger.info(f"🔗 Cleaned href (removed fragment): {href}")
        
        if href:
            logger.info(f"✅ Successfully extracted product link")
        else:
            logger.error(f"❌ No href found in link element")
        
        return href
                
    except Exception as e:
        logger.error(f"❌ Error getting product link: {str(e)}")
        logger.exception(e)  # Full traceback
        return None


def scrape_product_details(product_url: str, barcode: str) -> Dict[str, Any]:
    """Scrape product details from product page and download images"""
    logger.info(f"📄 Loading product page: {product_url}")
    driver.get(product_url)
    time.sleep(3)
    logger.info(f"✅ Product page loaded")
    
    # Create sanitized barcode for folder name (matches notebook logic)
    sanitized_barcode = barcode.replace(" ", "_").replace("/", "-").replace("\\", "-")
    images_folder = f"images/{sanitized_barcode}"
    
    product_data = {
        "product_url": product_url,
        "product_name": None,
        "price": None,
        "discount_percentage": None,
        "vat_percentage": None,
        "images_folder": images_folder,
        "images_downloaded": 0,
        "specifications": {}
    }
    
    try:
        # Extract product name
        try:
            logger.info("📝 Extracting product name...")
            h1_element = driver.find_element(By.CSS_SELECTOR, "h1.product-block__title")
            product_data["product_name"] = h1_element.text.strip()
            logger.info(f"✅ Product name: {product_data['product_name'][:50]}")
        except Exception as e:
            logger.warning(f"⚠️  Could not extract product name: {str(e)[:50]}")
            pass
        
        # Extract price
        try:
            logger.info("💰 Extracting price...")
            price_element = driver.find_element(By.CSS_SELECTOR, ".product-block__price-new, .listing-item__price-new")
            product_data["price"] = price_element.text.strip()
            logger.info(f"✅ Price: {product_data['price']}")
        except Exception as e:
            logger.warning(f"⚠️  Could not extract price: {str(e)[:50]}")
            pass
        
        # Extract discount percentage
        try:
            discount_element = driver.find_element(By.CSS_SELECTOR, ".product-block__discount, .discount-percentage")
            product_data["discount_percentage"] = discount_element.text.strip()
        except:
            product_data["discount_percentage"] = "N/A"
        
        # Extract VAT percentage
        try:
            vat_element = driver.find_element(By.CSS_SELECTOR, ".product-block__inkl, .listing-item__inkl")
            vat_text = vat_element.text.strip()
            vat_match = re.search(r'(\d+)%', vat_text)
            if vat_match:
                product_data["vat_percentage"] = vat_match.group(1) + "%"
            else:
                product_data["vat_percentage"] = vat_text
        except:
            product_data["vat_percentage"] = "N/A"
        
        # Extract all description items dynamically
        try:
            logger.info("📋 Extracting specifications...")
            description_items = driver.find_elements(By.CSS_SELECTOR, ".product-description__item")
            logger.info(f"📋 Found {len(description_items)} specification items")
            
            for item in description_items:
                try:
                    title_elem = item.find_element(By.CSS_SELECTOR, ".product-description__item-title")
                    value_elem = item.find_element(By.CSS_SELECTOR, ".product-description__item-value")
                    
                    title = title_elem.text.strip().replace(":", "").strip()
                    value = value_elem.text.strip()
                    
                    # Only store non-empty keys and values
                    if title and value:
                        column_name = title.replace(" ", "_").replace("[", "").replace("]", "").lower()
                        product_data["specifications"][column_name] = value
                except:
                    continue
        except:
            pass
        
        # Download product images (matches notebook logic)
        os.makedirs(images_folder, exist_ok=True)
        
        try:
            logger.info("🖼️  Extracting product images...")
            thumbnail_images = driver.find_elements(By.CSS_SELECTOR, ".product-gallery__image-list-item img")
            logger.info(f"🖼️  Found {len(thumbnail_images)} thumbnail images")
            image_urls = []
            
            for img_elem in thumbnail_images:
                img_url = None
                
                # Try srcset first (highest resolution)
                srcset = img_elem.get_attribute("srcset")
                if srcset:
                    parts = srcset.split(",")
                    for part in reversed(parts):
                        if "2x" in part or parts.index(part) == len(parts) - 1:
                            img_url = part.split()[0].strip()
                            break
                
                # Try data-srcset
                if not img_url:
                    data_srcset = img_elem.get_attribute("data-srcset")
                    if data_srcset:
                        parts = data_srcset.split(",")
                        for part in reversed(parts):
                            if "2x" in part or parts.index(part) == len(parts) - 1:
                                img_url = part.split()[0].strip()
                                break
                
                # Last resort: src
                if not img_url:
                    img_url = img_elem.get_attribute("src")
                
                if img_url and img_url.startswith("http"):
                    image_urls.append(img_url)
            
            # Download images to local folder
            downloaded_count = 0
            for idx, img_url in enumerate(image_urls, 1):
                try:
                    response = requests.get(img_url, timeout=10)
                    if response.status_code == 200:
                        ext = ".jpg"
                        if "." in img_url.split("/")[-1]:
                            url_filename = img_url.split("?")[0].split("/")[-1]
                            if "." in url_filename:
                                ext = "." + url_filename.split(".")[-1]
                        
                        # Use downloaded_count + 1 for sequential naming
                        filename = f"{images_folder}/image_{downloaded_count + 1}{ext}"
                        with open(filename, "wb") as f:
                            f.write(response.content)
                        downloaded_count += 1
                        logger.info(f"📥 Downloaded image {downloaded_count}: {filename}")
                except Exception as e:
                    logger.warning(f"⚠️  Failed to download image from URL {idx}: {str(e)[:80]}")
            
            product_data["images_downloaded"] = downloaded_count
            logger.info(f"✅ Downloaded {downloaded_count}/{len(image_urls)} images to {images_folder}")
        except Exception as e:
            logger.warning(f"⚠️  Could not extract/download images: {str(e)[:100]}")
            pass
            
    except Exception as e:
        logger.error(f"❌ Error scraping product: {str(e)}")
        logger.exception(e)
        raise Exception(f"Error scraping product: {str(e)}")
    
    return product_data


# ==============================
# REALOEM SCRAPER FUNCTIONS
# ==============================

def kill_overlays(driver):
    """Remove all modal popups, overlays, and enable scrolling"""
    driver.execute_script("""
        const selectors = [
            '.modal', '.popup', '.overlay', '.backdrop',
            '.cookie', '.cookies', '.consent',
            '[role="dialog"]',
            '[class*="modal"]',
            '[class*="popup"]',
            '[class*="overlay"]',
            '.ro-modal', '[data-ro]'
        ];

        selectors.forEach(sel => {
            document.querySelectorAll(sel).forEach(e => e.remove());
        });

        document.body.style.overflow = 'auto';
    """)


def handle_realoem_cookie():
    """Auto-accept cookies for realoem once"""
    global realoem_cookie_handled
    if realoem_cookie_handled:
        return

    try:
        cookie_btn = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(., 'Accept') or contains(., 'Agree')]")
            )
        )
        cookie_btn.click()
        realoem_cookie_handled = True
        logger.info("✅ Cookie consent accepted")
    except:
        pass


def handle_subscription_popup():
    """Handle subscription popup - click 'Maybe Later' or close button (aggressive)"""
    popup_closed = False
    
    # Try multiple selectors for "Maybe Later" button
    try:
        maybe_later = driver.find_element(By.CSS_SELECTOR, 'button[data-ro="later"]')
        maybe_later.click()
        time.sleep(0.2)  # Brief pause after click
        popup_closed = True
        logger.info("✅ Clicked 'Maybe Later' on subscription popup")
    except:
        pass
    
    # Try alternative selector
    if not popup_closed:
        try:
            maybe_later = driver.find_element(By.CSS_SELECTOR, 'button.ro-btn.ro-secondary[data-ro="later"]')
            maybe_later.click()
            time.sleep(0.2)
            popup_closed = True
            logger.info("✅ Clicked 'Maybe Later' (alternative selector)")
        except:
            pass
    
    # Try close button
    if not popup_closed:
        try:
            close_btn = driver.find_element(By.CSS_SELECTOR, 'button[data-ro="close"]')
            close_btn.click()
            time.sleep(0.2)
            popup_closed = True
            logger.info("✅ Closed subscription popup")
        except:
            pass
    
    # Try alternative close button selector
    if not popup_closed:
        try:
            close_btn = driver.find_element(By.CSS_SELECTOR, 'button.ro-close[data-ro="close"]')
            close_btn.click()
            time.sleep(0.2)
            popup_closed = True
            logger.info("✅ Closed subscription popup (alternative selector)")
        except:
            pass
    
    return popup_closed


def close_extra_tabs(driver):
    """Close any surprise popup tabs"""
    main = driver.window_handles[0]
    for h in driver.window_handles:
        if h != main:
            driver.switch_to.window(h)
            driver.close()
    driver.switch_to.window(main)


def aggressive_popup_killer(driver):
    """Aggressively remove all popups without waiting"""
    try:
        kill_overlays(driver)
        handle_subscription_popup()
        handle_realoem_cookie()
        close_extra_tabs(driver)
    except:
        pass


def safe_navigate_realoem(url: str):
    """Navigate to URL and apply all protection layers for realoem"""
    driver.get(url)
    aggressive_popup_killer(driver)


def scrape_realoem_barcode(barcode: str) -> Dict[str, Any]:
    """Scrape BMW part data from realoem.com"""
    logger.info(f"🔍 Scraping RealOEM for barcode: {barcode}")
    
    # Extract numeric portion only
    numeric_barcode = re.sub(r'\D', '', str(barcode))
    
    if not numeric_barcode:
        logger.error(f"❌ No numeric digits found in barcode: {barcode}")
        return {
            "success": False,
            "error": "Invalid barcode - no numeric digits found"
        }
    
    logger.info(f"📊 Using numeric barcode: {numeric_barcode}")
    
    url = f"https://www.realoem.com/bmw/enUS/partxref?q={numeric_barcode}"
    
    try:
        # Navigate to part search page
        safe_navigate_realoem(url)
        
        # Proactively kill popups before waiting
        time.sleep(0.5)
        aggressive_popup_killer(driver)
        
        # Wait for either error message OR content (whichever appears first)
        try:
            WebDriverWait(driver, 10).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, "div.error.vs2")) > 0 or 
                         len(d.find_elements(By.CSS_SELECTOR, "div.content h1")) > 0
            )
        except:
            # If timeout, aggressively kill popups and try again
            aggressive_popup_killer(driver)
            time.sleep(0.5)
            try:
                WebDriverWait(driver, 5).until(
                    lambda d: len(d.find_elements(By.CSS_SELECTOR, "div.error.vs2")) > 0 or 
                             len(d.find_elements(By.CSS_SELECTOR, "div.content h1")) > 0
                )
            except:
                pass
        
        # Immediately kill any popups that appeared
        aggressive_popup_killer(driver)
        
        # Check if part was not found (check error FIRST before trying to extract data)
        part_not_found = False
        try:
            error_div = driver.find_element(By.CSS_SELECTOR, "div.error.vs2")
            error_text = error_div.text.strip()
            
            if "not found" in error_text.lower():
                logger.warning(f"⚠️ Part not found: {error_text}")
                return {
                    "part_number": "NOT FOUND",
                    "description": error_text,
                    "from_date": "",
                    "to_date": "",
                    "weight": "",
                    "price": "",
                    "vehicle_count": 0,
                    "first_vehicle_tags": ""
                }
        except:
            # No error div found, continue with scraping
            pass
        
        # Extract part data - verify content exists first
        try:
            part_number = driver.find_element(By.CSS_SELECTOR, "div.content h1").text
            description = driver.find_element(By.CSS_SELECTOR, "div.content h2").text
        except:
            # Content not loaded properly
            logger.error("❌ Content failed to load (timeout or popup blocking)")
            return {
                "success": False,
                "error": "Content failed to load (timeout or popup blocking)"
            }
        
        logger.info(f"✅ Part Number: {part_number}")
        logger.info(f"✅ Description: {description}")
        
        # Extract details from dl
        part_details = {}
        try:
            dt_elements = driver.find_elements(By.CSS_SELECTOR, "div.content dl dt")
            dd_elements = driver.find_elements(By.CSS_SELECTOR, "div.content dl dd")
            
            for dt, dd in zip(dt_elements, dd_elements):
                key = dt.text.replace(":", "").strip()
                value = dd.text.strip() if dd.text.strip() else "-"
                part_details[key] = value
                logger.info(f"  {key}: {value}")
        except Exception as e:
            logger.warning(f"⚠️ Error extracting details: {str(e)[:50]}")
        
        # Extract vehicle links
        vehicle_links_list = []
        try:
            links = driver.find_elements(By.CSS_SELECTOR, "div.partSearchResults ul li a")
            for link in links:
                vehicle_text = link.text
                vehicle_url = link.get_attribute("href")
                vehicle_links_list.append({"text": vehicle_text, "url": vehicle_url})
            logger.info(f"✅ Found {len(vehicle_links_list)} vehicles")
        except Exception as e:
            logger.warning(f"⚠️ Error extracting vehicle links: {str(e)[:50]}")
        
        # Navigate to first vehicle link if exists
        vehicle_tags = ""
        if vehicle_links_list:
            first_vehicle_url = vehicle_links_list[0]["url"]
            logger.info(f"🚗 Navigating to first vehicle...")
            safe_navigate_realoem(first_vehicle_url)
            
            # Proactively kill popups before waiting
            time.sleep(0.5)
            aggressive_popup_killer(driver)
            
            # Extract first vehicle tags
            try:
                results_section = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "partSearchResults"))
                )
                first_li = results_section.find_element(By.CSS_SELECTOR, "ul li:first-child")
                full_text = first_li.text.strip()
                
                if ':' in full_text:
                    vehicle_tags = full_text.split(':')[0].strip()
                else:
                    vehicle_tags = full_text
                    
                logger.info(f"✅ Vehicle tags: {vehicle_tags}")
            except Exception as e:
                logger.warning(f"⚠️ Error extracting vehicle tags: {str(e)[:50]}")
                vehicle_tags = "N/A"
        
        # Return structured data
        return {
            "part_number": part_number,
            "description": description,
            "from_date": part_details.get("From", ""),
            "to_date": part_details.get("To", ""),
            "weight": part_details.get("Weight", ""),
            "price": part_details.get("Price", ""),
            "vehicle_count": len(vehicle_links_list),
            "first_vehicle_tags": vehicle_tags
        }
        
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e).split('\n')[0][:200]
        logger.error(f"❌ Error scraping RealOEM: {error_type}: {error_msg}")
        return {
            "success": False,
            "error": f"{error_type}: {error_msg}"
        }


@app.get("/")
async def root():
    """API root endpoint"""
    return {
        "message": "Auto Parts Scraper API",
        "version": "1.0.0",
        "scrapers": ["autodoc", "realoem"],
        "endpoints": {
            "scrape_post": {
                "method": "POST",
                "path": "/scrape",
                "body": {
                    "barcode": "string (required)",
                    "scraper": "string (optional, default: 'autodoc', options: 'autodoc' or 'realoem')"
                }
            },
            "scrape_get": {
                "method": "GET",
                "path": "/scrape/{barcode}",
                "query_params": {
                    "scraper": "string (optional, default: 'autodoc', options: 'autodoc' or 'realoem')"
                },
                "example": "/scrape/34116860912?scraper=realoem"
            },
            "health": {
                "method": "GET",
                "path": "/health"
            }
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "browser_active": driver is not None
    }


@app.get("/images/{barcode}/{image_number}")
async def get_image(barcode: str, image_number: int):
    """Serve images for a specific barcode"""
    # Sanitize barcode for folder name (same as scraper logic)
    sanitized_barcode = barcode.replace(" ", "_").replace("/", "-").replace("\\", "-")
    image_folder = f"images/{sanitized_barcode}"
    
    # Try common image extensions
    for ext in ['.jpg', '.jpeg', '.png', '.webp']:
        image_path = os.path.join(image_folder, f"image_{image_number}{ext}")
        if os.path.exists(image_path):
            return FileResponse(image_path)
    
    # If no image found, return 404
    raise HTTPException(status_code=404, detail="Image not found")


@app.post("/scrape", response_model=ProductResponse)
def scrape_barcode(request: BarcodeRequest):
    """
    Scrape product data for a given barcode.
    Supports both autodoc and realoem scrapers.
    Sync handler — uvicorn runs this in a threadpool automatically.
    """
    barcode = request.barcode.strip()
    scraper = request.scraper.lower()
    
    if not barcode:
        logger.error("❌ Empty barcode provided")
        raise HTTPException(status_code=400, detail="Barcode cannot be empty")
    
    if scraper not in ["autodoc", "realoem"]:
        logger.error(f"❌ Invalid scraper: {scraper}")
        raise HTTPException(status_code=400, detail="Scraper must be 'autodoc' or 'realoem'")
    
    logger.info(f"🎯 Processing request for barcode: {barcode} using {scraper.upper()} scraper")
    
    # Lazy-init driver on first request
    initialize_driver()
    
    try:
        if scraper == "realoem":
            # Use RealOEM scraper
            result = scrape_realoem_barcode(barcode)
            
            # Check if result has error
            if result.get("success") == False or result.get("error"):
                logger.error(f"❌ RealOEM scraping failed: {result.get('error', 'Unknown error')}")
                return ProductResponse(
                    success=False,
                    barcode=barcode,
                    scraper=scraper,
                    error=result.get('error', 'Failed to scrape RealOEM')
                )
            
            logger.info(f"✅ Successfully scraped RealOEM data")
            return ProductResponse(
                success=True,
                barcode=barcode,
                scraper=scraper,
                data=result
            )
            
        else:  # autodoc
            # Warm up browser for autodoc if first time
            warmup_autodoc()
            
            # Navigate to search page
            url = f"https://www.autodoc.co.uk/spares-search?keyword={barcode}"
            logger.info(f"🌐 Navigating to: {url}")
            driver.get(url)
            
            logger.info("⏳ Waiting for page body to load...")
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            logger.info("✅ Page body loaded")
            
            # Check for Cloudflare challenge
            _wait_for_cloudflare(max_wait=60)
            
            handle_cookies()
            
            logger.info("⏳ Waiting for search results to render...")
            time.sleep(3)
            
            # Get first product link
            product_link = get_first_product_link(barcode)
            
            if not product_link:
                logger.error(f"❌ No product link found for barcode: {barcode}")
                return ProductResponse(
                    success=False,
                    barcode=barcode,
                    scraper=scraper,
                    error="No product found for this barcode"
                )
            
            logger.info(f"✅ Product link found: {product_link}")
            
            # Scrape product details
            product_data = scrape_product_details(product_link, barcode)
            
            logger.info(f"✅ Successfully scraped product data")
            logger.info(f"📊 Data summary: {len(product_data)} fields, {len(product_data.get('image_urls', []))} images")
            
            return ProductResponse(
                success=True,
                barcode=barcode,
                scraper=scraper,
                data=product_data
            )
        
    except Exception as e:
        logger.error(f"❌ Error processing barcode {barcode}: {str(e)}")
        logger.exception(e)
        return ProductResponse(
            success=False,
            barcode=barcode,
            scraper=scraper,
            error=str(e)
        )


@app.get("/scrape/{barcode}")
def scrape_barcode_get(barcode: str, scraper: str = "autodoc"):
    """
    Scrape product data for a given barcode (GET method)
    Query parameter 'scraper' can be 'autodoc' or 'realoem' (default: autodoc)
    Example: /scrape/34116860912?scraper=realoem
    """
    request = BarcodeRequest(barcode=barcode, scraper=scraper)
    return scrape_barcode(request)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
