from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import os
import time
import re
import subprocess
import threading
import logging
import requests
import atexit
import signal
from typing import Optional, Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Per-scraper timeouts (seconds)
AUTODOC_TIMEOUT = 120   # 2 minutes
REALOEM_TIMEOUT = 300   # 5 minutes

# Pool config
POOL_MAX_INSTANCES = 3          # 1 permanent + up to 2 temp
IDLE_KILL_AFTER    = 300        # kill temp instance after 5 min idle
IDLE_CHECK_EVERY   = 60         # reaper runs every 60s


# ══════════════════════════════════════════════════════════════════
#  BrowserInstance — one Chrome session with its own state
# ══════════════════════════════════════════════════════════════════

class BrowserInstance:
    _id_counter = 0
    _id_lock = threading.Lock()

    def __init__(self, permanent: bool = False):
        with BrowserInstance._id_lock:
            BrowserInstance._id_counter += 1
            self.id = BrowserInstance._id_counter

        self.permanent = permanent
        self.lock = threading.Lock()          # one scrape at a time per instance
        self._warmup_lock = threading.Lock()  # prevents concurrent warmup calls
        self.driver: Optional[Any] = None
        self.wait: Optional[Any] = None
        self.autodoc_cookie_handled = False
        self.realoem_cookie_handled = False
        self.last_used: float = time.time()
        self._alive = False

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self):
        """Spin up Chrome for this instance."""
        logger.info(f"🚀 [inst-{self.id}] Starting Chrome...")
        # SeleniumBase Driver with built-in stealth features
        self.driver = Driver(uc=True, headless=False)
        self.wait   = WebDriverWait(self.driver, 15)
        self._alive = True
        # Store chromedriver service PID so we can force-kill it on quit
        try:
            self._service_pid = self.driver.service.process.pid
        except Exception:
            self._service_pid = None
        logger.info(f"✅ [inst-{self.id}] Chrome ready (service PID: {self._service_pid})")

    def quit(self):
        """Shut down Chrome cleanly, then force-kill any surviving processes."""
        self._alive = False
        service_pid = getattr(self, '_service_pid', None)
        if self.driver is not None:
            try:
                self.driver.quit()
                logger.info(f"🛑 [inst-{self.id}] Chrome closed")
            except Exception:
                pass
            self.driver = None
            self.wait   = None
        # Force-kill the chromedriver service process in case driver.quit() left it alive
        if service_pid:
            try:
                subprocess.run(["kill", "-9", str(service_pid)],
                               check=False, capture_output=True)
                logger.info(f"🧹 [inst-{self.id}] Force-killed service PID {service_pid}")
            except Exception:
                pass
            self._service_pid = None

    def is_alive(self) -> bool:
        if not self._alive or self.driver is None:
            return False
        try:
            _ = self.driver.title
            return True
        except Exception:
            self._alive = False
            return False

    def revive(self):
        """Quit dead session and start a fresh one."""
        logger.warning(f"🔄 [inst-{self.id}] Reviving dead session...")
        self.quit()
        self.autodoc_cookie_handled = False
        self.realoem_cookie_handled = False
        self.start()

    def touch(self):
        """Update last-used timestamp."""
        self.last_used = time.time()

    def warmup_autodoc(self):
        """Visit autodoc homepage and handle cookies (once per instance)."""
        with self._warmup_lock:
            if self.autodoc_cookie_handled:
                return
            logger.info(f"🌐 [inst-{self.id}] Warming up autodoc...")
            self.driver.get("https://www.autodoc.co.uk/")
            _wait_for_cloudflare(self)
            _handle_cookies(self)
            logger.info(f"✅ [inst-{self.id}] Autodoc warm")


# ══════════════════════════════════════════════════════════════════
#  BrowserPool — manages the permanent + temp instances
# ══════════════════════════════════════════════════════════════════

class BrowserPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._instances: list[BrowserInstance] = []
        self._reaper: Optional[threading.Thread] = None
        self._stopped = False

    # ── Startup / shutdown ─────────────────────────────────────

    def start(self):
        """Create permanent instance and start idle reaper."""
        _kill_orphaned_chromedrivers()
        perm = BrowserInstance(permanent=True)
        perm.start()
        # Pre-lock during warmup so no request can use the driver while
        # the background thread is navigating (prevents two-thread driver race).
        perm.lock.acquire()
        with self._lock:
            self._instances.append(perm)

        def _warmup_perm():
            try:
                perm.warmup_autodoc()
            except Exception as e:
                logger.warning(f"⚠️  [inst-{perm.id}] Background warmup failed (non-fatal): {e}")
            finally:
                try:
                    perm.lock.release()
                except RuntimeError:
                    pass

        threading.Thread(target=_warmup_perm, daemon=True).start()
        self._reaper = threading.Thread(target=self._idle_reaper, daemon=True)
        self._reaper.start()
        logger.info("✅ BrowserPool started (1 permanent instance, warming up in background)")

    def shutdown(self):
        """Quit every instance. Safe to call multiple times."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            instances = list(self._instances)
        for inst in instances:
            inst.quit()
        with self._lock:
            self._instances.clear()
        # Mop up any Chrome/chromedriver processes that survived driver.quit()
        _kill_orphaned_chromedrivers()
        logger.info("🛑 BrowserPool shut down")

    # ── Acquire / release ──────────────────────────────────────

    def acquire(self, timeout: float = 30.0) -> "BrowserInstance":
        """
        Return a free BrowserInstance (its lock already acquired).
        - Try existing idle instances first.
        - If all busy and under cap, spawn ONE temp instance, then keep
          polling until it (or another) becomes available.
        - Raises RuntimeError if nothing becomes free within *timeout*.
        """
        deadline = time.time() + timeout
        spawned = False                       # prevent same caller spawning multiple temps

        while time.time() < deadline:
            inst = None
            pool_size = 0

            with self._lock:
                pool_size = len(self._instances)
                for candidate in self._instances:
                    if candidate.lock.acquire(blocking=False):
                        inst = candidate
                        break

                # All busy — spawn ONE temp if we haven’t already and are under cap
                if inst is None and not spawned and pool_size < POOL_MAX_INSTANCES:
                    new_inst = BrowserInstance(permanent=False)
                    new_inst.lock.acquire()          # pre-lock until Chrome is ready
                    self._instances.append(new_inst)
                    pool_size = len(self._instances)
                    spawned = True
                    threading.Thread(
                        target=self._start_instance, args=(new_inst,), daemon=True
                    ).start()
                    logger.info(f"🆕 [inst-{new_inst.id}] Spawning temp instance ({pool_size} total)")

            # ── Outside pool lock ───────────────────────────────────
            if inst is not None:
                # Health check / revive happens outside the pool lock
                # so other threads aren’t blocked while Chrome restarts.
                if not inst.is_alive():
                    inst.revive()
                    inst.autodoc_cookie_handled = False
                    inst.realoem_cookie_handled = False
                inst.touch()
                logger.info(f"🟢 [inst-{inst.id}] Acquired from pool ({pool_size} total)")
                return inst

            time.sleep(0.5)

        raise RuntimeError("BrowserPool: no instance available within timeout")

    def release(self, inst: BrowserInstance):
        """Release lock so the instance can be used by another request."""
        inst.touch()
        try:
            inst.lock.release()
        except RuntimeError:
            pass  # already released
        logger.info(f"🔵 [inst-{inst.id}] Released back to pool")

    # ── Internal helpers ───────────────────────────────────────

    def _start_instance(self, inst: BrowserInstance):
        """Start Chrome for a temp instance — runs in a background thread.
        The instance lock was pre-acquired by acquire(); we release it once
        Chrome is live.  Warmup is NOT done here — it happens on-demand when
        the first request actually uses the instance (inside _run_scrape).
        This keeps the pre-lock window short (just Chrome startup, ~5-10 s)."""
        try:
            inst.start()
            logger.info(f"✅ [inst-{inst.id}] Temp instance ready")
        except Exception as e:
            logger.error(f"❌ [inst-{inst.id}] Chrome failed to start: {e}")
            with self._lock:
                if inst in self._instances:
                    self._instances.remove(inst)
            inst.quit()
        finally:
            # Always release so acquire() isn’t left spinning forever
            try:
                inst.lock.release()
            except RuntimeError:
                pass

    def _idle_reaper(self):
        """Background thread: kill temp instances idle for > IDLE_KILL_AFTER seconds."""
        while True:
            time.sleep(IDLE_CHECK_EVERY)
            now = time.time()
            with self._lock:
                to_kill = [
                    inst for inst in self._instances
                    if not inst.permanent
                    and not inst.lock.locked()
                    and (now - inst.last_used) > IDLE_KILL_AFTER
                ]
                for inst in to_kill:
                    self._instances.remove(inst)
            for inst in to_kill:
                logger.info(f"🗑️  [inst-{inst.id}] Idle reaper killing temp instance")
                inst.quit()

    def status(self) -> dict:
        with self._lock:
            return {
                "total": len(self._instances),
                "busy":  sum(1 for i in self._instances if i.lock.locked()),
                "idle":  sum(1 for i in self._instances if not i.lock.locked()),
                "permanent": sum(1 for i in self._instances if i.permanent),
                "temp": sum(1 for i in self._instances if not i.permanent),
            }


# Singleton pool
pool = BrowserPool()


def _emergency_shutdown(signum=None, frame=None):
    """Called on SIGTERM (uvicorn reload/stop) or process exit as a safety net."""
    logger.info(f"🚨 Emergency shutdown triggered (signal={signum}) — killing all browsers...")
    pool.shutdown()

# Safety net: catches uvicorn --reload kills and normal process exits
atexit.register(_emergency_shutdown)
signal.signal(signal.SIGTERM, _emergency_shutdown)


# ══════════════════════════════════════════════════════════════════
#  Utility helpers (now receive inst instead of using globals)
# ══════════════════════════════════════════════════════════════════

def _kill_orphaned_chromedrivers():
    """Kill chromedriver + Chrome browser processes left behind by a previous server run."""
    targets = [
        "chromedriver",
        "Google Chrome for Testing",  # undetected_chromedriver's browser binary
    ]
    for target in targets:
        try:
            result = subprocess.run(
                ["pgrep", "-f", target], capture_output=True, text=True
            )
            pids = [p for p in result.stdout.strip().split() if p]
            # Exclude the current process and its parent to avoid self-kill
            current_pid = str(os.getpid())
            pids = [p for p in pids if p != current_pid]
            for pid in pids:
                try:
                    subprocess.run(["kill", "-9", pid], check=False)
                    logger.info(f"🧹 Killed orphaned '{target}' PID {pid}")
                except Exception:
                    pass
            if not pids:
                logger.info(f"✅ No orphaned '{target}' processes found")
        except Exception as e:
            logger.warning(f"⚠️  Could not scan for '{target}' processes: {e}")


def _wait_for_cloudflare(inst: BrowserInstance, max_wait: int = 60) -> bool:
    """Wait for Cloudflare challenge to complete on this instance."""
    start = time.time()
    while time.time() - start < max_wait:
        try:
            title = inst.driver.title
        except Exception:
            time.sleep(2)
            continue
        if "Just a moment" in title or "Checking your browser" in title:
            elapsed = int(time.time() - start)
            logger.warning(f"⚠️  [inst-{inst.id}] Cloudflare active... ({elapsed}s)")
            time.sleep(3)
        else:
            logger.info(f"✅ [inst-{inst.id}] Cloudflare passed")
            return True
    logger.error(f"❌ [inst-{inst.id}] Cloudflare timed out")
    return False


def _handle_cookies(inst: BrowserInstance):
    """Handle autodoc cookie consent popup for this instance."""
    if inst.autodoc_cookie_handled:
        return
    try:
        cookie_btn = inst.wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, 'button[data-cookies="allow_all_cookies"]')
            )
        )
        inst.driver.execute_script("arguments[0].click();", cookie_btn)
        inst.autodoc_cookie_handled = True
        logger.info(f"✅ [inst-{inst.id}] Cookie consent accepted")
        time.sleep(1)
    except Exception as e:
        logger.warning(f"⚠️  [inst-{inst.id}] No cookie popup: {str(e)[:80]}")
        inst.autodoc_cookie_handled = True  # Don't retry endlessly


@asynccontextmanager
async def lifespan(app):
    """Start the browser pool on startup, shut it all down on exit."""
    logger.info("🚀 Server starting up — initialising BrowserPool...")
    pool.start()
    yield
    logger.info("🛑 Server shutting down — closing all browser instances...")
    pool.shutdown()


app = FastAPI(
    title="Auto Parts Scraper API",
    description="API for scraping car parts data from autodoc.parts",
    version="1.0.0",
    lifespan=lifespan,
)


class BarcodeRequest(BaseModel):
    barcode: str
    scraper: str = "autodoc"  # Default to autodoc, options: "autodoc" or "realoem"


# ── Autodoc nested models ────────────────────────────────────────────────────

class AutodocProduct(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None

class AutodocPricing(BaseModel):
    price: Optional[str] = None
    discount_percentage: Optional[str] = None
    vat_percentage: Optional[str] = None

class AutodocMedia(BaseModel):
    images_folder: Optional[str] = None
    images_downloaded: int = 0

class AutodocOENumber(BaseModel):
    number: str
    brand: Optional[str] = None
    url: Optional[str] = None

class AutodocData(BaseModel):
    product: AutodocProduct = AutodocProduct()
    pricing: AutodocPricing = AutodocPricing()
    media: AutodocMedia = AutodocMedia()
    specifications: Dict[str, Any] = {}
    compatibility: Dict[str, Any] = {}
    oe_numbers: list[AutodocOENumber] = []

# ── RealOEM nested models ─────────────────────────────────────────────────────

class RealOEMProduct(BaseModel):
    part_number: Optional[str] = None
    description: Optional[str] = None

class RealOEMPricing(BaseModel):
    price: Optional[str] = None

class RealOEMDetails(BaseModel):
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    weight: Optional[str] = None

class RealOEMCompatibility(BaseModel):
    vehicle_count: int = 0
    first_vehicle_tags: Optional[str] = None

class RealOEMData(BaseModel):
    product: RealOEMProduct = RealOEMProduct()
    pricing: RealOEMPricing = RealOEMPricing()
    details: RealOEMDetails = RealOEMDetails()
    compatibility: RealOEMCompatibility = RealOEMCompatibility()

# ── Top-level response ────────────────────────────────────────────────────────

class ProductResponse(BaseModel):
    success: bool
    barcode: str
    scraper: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


def get_first_product_link(inst: BrowserInstance, barcode: str) -> Optional[str]:
    """Get the first product link from search results"""
    try:
        logger.info(f"🔍 [inst-{inst.id}] Waiting for search results to load...")
        time.sleep(2)

        listing_items = inst.driver.find_elements(By.CSS_SELECTOR, ".listing-item__wrap")
        logger.info(f"📦 [inst-{inst.id}] Found {len(listing_items)} listing items")

        if not listing_items:
            logger.warning(f"⚠️  [inst-{inst.id}] No products found for barcode: {barcode}")
            alt_items = inst.driver.find_elements(By.CSS_SELECTOR, ".listing-item, [data-product-id]")
            logger.info(f"🔄 [inst-{inst.id}] Alternative selector found {len(alt_items)} items")
            if not alt_items:
                logger.error(f"❌ [inst-{inst.id}] No products found with any selector")
                return None
            listing_items = alt_items

        first_item = listing_items[0]
        try:
            title_link = first_item.find_element(By.CSS_SELECTOR, ".listing-item__name")
        except Exception as e1:
            logger.warning(f"⚠️  [inst-{inst.id}] Primary selector failed: {str(e1)[:50]}")
            try:
                title_link = first_item.find_element(By.CSS_SELECTOR, "a.listing-item__name, [data-link]")
            except Exception as e2:
                logger.error(f"❌ [inst-{inst.id}] Alt selector failed: {str(e2)[:50]}")
                title_link = first_item.find_element(By.TAG_NAME, "a")

        href = title_link.get_attribute("href") or title_link.get_attribute("data-link")
        if href and "#" in href:
            href = href.split("#")[0]
        logger.info(f"🔗 [inst-{inst.id}] Product link: {href}")
        return href

    except Exception as e:
        logger.error(f"❌ [inst-{inst.id}] Error getting product link: {str(e)}")
        logger.exception(e)
        return None


def scrape_product_details(inst: BrowserInstance, product_url: str, barcode: str) -> Dict[str, Any]:
    """Scrape product details from product page and download images"""
    logger.info(f"📄 [inst-{inst.id}] Loading product page: {product_url}")
    inst.driver.get(product_url)
    time.sleep(3)
    logger.info(f"✅ [inst-{inst.id}] Product page loaded")
    
    # Create sanitized barcode for folder name (matches notebook logic)
    sanitized_barcode = barcode.replace(" ", "_").replace("/", "-").replace("\\", "-")
    images_folder = f"images/{sanitized_barcode}"
    
    product_data = {
        "product": {
            "name": None,
            "url": product_url,
        },
        "pricing": {
            "price": None,
            "discount_percentage": None,
            "vat_percentage": None,
        },
        "media": {
            "images_folder": images_folder,
            "images_downloaded": 0,
        },
        "specifications": {},
        "compatibility": {},
        "oe_numbers": []
    }
    
    try:
        # Extract product name
        try:
            logger.info(f"📝 [inst-{inst.id}] Extracting product name...")
            h1_element = inst.driver.find_element(By.CSS_SELECTOR, "h1.product-block__title")
            product_data["product"]["name"] = h1_element.text.strip()
            logger.info(f"✅ [inst-{inst.id}] Product name: {product_data['product']['name'][:50]}")
        except Exception as e:
            logger.warning(f"⚠️  [inst-{inst.id}] Could not extract product name: {str(e)[:50]}")

        # Extract price
        try:
            logger.info(f"💰 [inst-{inst.id}] Extracting price...")
            price_element = inst.driver.find_element(By.CSS_SELECTOR, ".product-block__price-new, .listing-item__price-new")
            product_data["pricing"]["price"] = price_element.text.strip()
            logger.info(f"✅ [inst-{inst.id}] Price: {product_data['pricing']['price']}")
        except Exception as e:
            logger.warning(f"⚠️  [inst-{inst.id}] Could not extract price: {str(e)[:50]}")

        # Extract discount percentage
        try:
            discount_element = inst.driver.find_element(By.CSS_SELECTOR, ".product-block__discount, .discount-percentage")
            product_data["pricing"]["discount_percentage"] = discount_element.text.strip()
        except:
            product_data["pricing"]["discount_percentage"] = "N/A"

        # Extract VAT percentage
        try:
            vat_element = inst.driver.find_element(By.CSS_SELECTOR, ".product-block__inkl, .listing-item__inkl")
            vat_text = vat_element.text.strip()
            vat_match = re.search(r'(\d+)%', vat_text)
            if vat_match:
                product_data["pricing"]["vat_percentage"] = vat_match.group(1) + "%"
            else:
                product_data["pricing"]["vat_percentage"] = vat_text
        except:
            product_data["pricing"]["vat_percentage"] = "N/A"

        # Extract all description items dynamically
        try:
            logger.info(f"📋 [inst-{inst.id}] Extracting specifications...")
            description_items = inst.driver.find_elements(By.CSS_SELECTOR, ".product-description__item")
            logger.info(f"📋 [inst-{inst.id}] Found {len(description_items)} specification items")
            for item in description_items:
                try:
                    title_elem = item.find_element(By.CSS_SELECTOR, ".product-description__item-title")
                    value_elem = item.find_element(By.CSS_SELECTOR, ".product-description__item-value")
                    title = title_elem.text.strip().replace(":", "").strip()
                    value = value_elem.text.strip()
                    if title and value:
                        column_name = title.replace(" ", "_").replace("[", "").replace("]", "").lower()
                        product_data["specifications"][column_name] = value
                except:
                    continue
        except:
            pass

        # Extract summary table
        try:
            logger.info(f"📊 [inst-{inst.id}] Extracting summary table...")
            summary = {}
            rows = inst.driver.find_elements(By.CSS_SELECTOR, ".summary-table tr")
            ARRAY_FIELD_SEPARATORS = {"car_models": ";", "engines": ";", "oe_part_numbers": ","}
            for row in rows:
                try:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) == 2:
                        key = cells[0].text.strip()
                        value = cells[1].text.strip()
                        if key and value:
                            col_name = (
                                key.replace(" ", "_").replace("(", "").replace(")", "")
                                   .replace("/", "_").replace("-", "_").lower()
                            )
                            if col_name in ARRAY_FIELD_SEPARATORS:
                                sep = ARRAY_FIELD_SEPARATORS[col_name]
                                summary[col_name] = [v.strip() for v in value.split(sep) if v.strip()]
                            else:
                                summary[col_name] = value
                except:
                    continue
            product_data["compatibility"] = summary
            logger.info(f"✅ [inst-{inst.id}] Extracted {len(summary)} compatibility fields")
        except Exception as e:
            logger.warning(f"⚠️  [inst-{inst.id}] Could not extract summary table: {str(e)[:100]}")
            product_data["compatibility"] = {}

        # Extract OE numbers
        try:
            logger.info(f"🔢 [inst-{inst.id}] Extracting OE numbers...")
            oe_numbers = []
            oe_links = inst.driver.find_elements(By.CSS_SELECTOR, "#oem .product-oem__link")
            for link in oe_links:
                try:
                    text = link.text.strip()
                    href = link.get_attribute("href") or ""
                    if " — " in text:
                        number_part, brand_part = text.split(" — ", 1)
                        number = number_part.replace("OE", "").strip()
                    else:
                        number = text.replace("OE", "").strip()
                        brand_part = ""
                    if number:
                        oe_numbers.append({"number": number, "brand": brand_part.strip(), "url": href})
                except:
                    continue
            product_data["oe_numbers"] = oe_numbers
            logger.info(f"✅ [inst-{inst.id}] Extracted {len(oe_numbers)} OE numbers")
        except Exception as e:
            logger.warning(f"⚠️  [inst-{inst.id}] Could not extract OE numbers: {str(e)[:100]}")
            product_data["oe_numbers"] = []

        # Download product images
        os.makedirs(images_folder, exist_ok=True)
        try:
            logger.info(f"🖼️  [inst-{inst.id}] Extracting product images...")
            IMAGE_SELECTORS = [
                ".product-gallery__image-list-item img",
                ".product-gallery__image-wrap img",
                ".product-gallery img",
                "img[data-srcset*='cdn.autodoc']",
                "img[srcset*='cdn.autodoc']",
            ]
            img_elems = []
            for selector in IMAGE_SELECTORS:
                found = inst.driver.find_elements(By.CSS_SELECTOR, selector)
                if found:
                    img_elems = found
                    logger.info(f"🖼️  [inst-{inst.id}] Found {len(img_elems)} image(s) via: {selector}")
                    break
            if not img_elems:
                logger.warning(f"⚠️  [inst-{inst.id}] No product images found")

            def _best_url_from_srcset(srcset_str: str) -> Optional[str]:
                """Pick the highest-resolution URL from a srcset string."""
                if not srcset_str:
                    return None
                # Each entry is "<url> <descriptor>", separated by commas.
                # We iterate in reverse to prefer 2x / higher descriptors.
                parts = [p.strip() for p in srcset_str.split(",") if p.strip()]
                for part in reversed(parts):
                    tokens = part.split()
                    if tokens:
                        return tokens[0]  # URL is always first token
                return None

            # Collect unique image URLs (preserve order, skip duplicates)
            seen_urls: set = set()
            image_urls = []
            for img_elem in img_elems:
                img_url = (
                    _best_url_from_srcset(img_elem.get_attribute("srcset"))
                    or _best_url_from_srcset(img_elem.get_attribute("data-srcset"))
                    or img_elem.get_attribute("src")
                )
                if img_url and img_url.startswith("http") and img_url not in seen_urls:
                    seen_urls.add(img_url)
                    image_urls.append(img_url)

            logger.info(f"🖼️  {len(image_urls)} unique image URL(s) collected")

            # Download images to local folder
            downloaded_count = 0
            for idx, img_url in enumerate(image_urls, 1):
                try:
                    response = requests.get(img_url, timeout=10)
                    if response.status_code == 200:
                        # Detect extension from URL path; fall back to .jpg for
                        # query-string-only URLs like cdn.autodoc.de/thumb?id=...
                        url_path = img_url.split("?")[0]
                        path_basename = url_path.split("/")[-1]
                        ext = ("." + path_basename.rsplit(".", 1)[-1]) if "." in path_basename else ".jpg"

                        filename = f"{images_folder}/image_{downloaded_count + 1}{ext}"
                        with open(filename, "wb") as f:
                            f.write(response.content)
                        downloaded_count += 1
                        logger.info(f"📥 Downloaded image {downloaded_count}: {filename}")
                    else:
                        logger.warning(f"⚠️  Image {idx} returned HTTP {response.status_code}: {img_url[:80]}")
                except Exception as e:
                    logger.warning(f"⚠️  Failed to download image {idx}: {str(e)[:80]}")

            product_data["media"]["images_downloaded"] = downloaded_count
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

def kill_overlays(inst: BrowserInstance):
    """Remove all modal popups, overlays, and enable scrolling"""
    inst.driver.execute_script("""
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


def handle_realoem_cookie(inst: BrowserInstance):
    """Auto-accept cookies for realoem once per instance"""
    if inst.realoem_cookie_handled:
        return
    try:
        cookie_btn = WebDriverWait(inst.driver, 3).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(., 'Accept') or contains(., 'Agree')]")
            )
        )
        cookie_btn.click()
        inst.realoem_cookie_handled = True
        logger.info(f"✅ [inst-{inst.id}] RealOEM cookie accepted")
    except:
        inst.realoem_cookie_handled = True


def handle_subscription_popup(inst: BrowserInstance):
    """Handle subscription popup — click 'Maybe Later' or close button"""
    for selector in [
        'button[data-ro="later"]',
        'button.ro-btn.ro-secondary[data-ro="later"]',
        'button[data-ro="close"]',
        'button.ro-close[data-ro="close"]',
    ]:
        try:
            btn = inst.driver.find_element(By.CSS_SELECTOR, selector)
            btn.click()
            time.sleep(0.2)
            logger.info(f"✅ [inst-{inst.id}] Closed popup via {selector}")
            return True
        except:
            continue
    return False


def close_extra_tabs(inst: BrowserInstance):
    """Close any surprise popup tabs"""
    main = inst.driver.window_handles[0]
    for h in inst.driver.window_handles:
        if h != main:
            inst.driver.switch_to.window(h)
            inst.driver.close()
    inst.driver.switch_to.window(main)


def aggressive_popup_killer(inst: BrowserInstance):
    """Aggressively remove all popups without waiting"""
    try:
        kill_overlays(inst)
        handle_subscription_popup(inst)
        handle_realoem_cookie(inst)
        close_extra_tabs(inst)
    except:
        pass


def safe_navigate_realoem(inst: BrowserInstance, url: str):
    """Navigate to URL and apply all protection layers for realoem"""
    inst.driver.get(url)
    aggressive_popup_killer(inst)


def scrape_realoem_barcode(inst: BrowserInstance, barcode: str) -> Dict[str, Any]:
    """Scrape BMW part data from realoem.com"""
    logger.info(f"🔍 [inst-{inst.id}] Scraping RealOEM for barcode: {barcode}")

    numeric_barcode = re.sub(r'\D', '', str(barcode))
    if not numeric_barcode:
        logger.error(f"❌ [inst-{inst.id}] No numeric digits found in barcode: {barcode}")
        return {"success": False, "error": "Invalid barcode - no numeric digits found"}

    logger.info(f"📊 [inst-{inst.id}] Using numeric barcode: {numeric_barcode}")
    url = f"https://www.realoem.com/bmw/enUS/partxref?q={numeric_barcode}"

    try:
        safe_navigate_realoem(inst, url)
        time.sleep(0.5)
        aggressive_popup_killer(inst)

        try:
            WebDriverWait(inst.driver, 10).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, "div.error.vs2")) > 0 or
                          len(d.find_elements(By.CSS_SELECTOR, "div.content h1")) > 0
            )
        except:
            aggressive_popup_killer(inst)
            time.sleep(0.5)
            try:
                WebDriverWait(inst.driver, 5).until(
                    lambda d: len(d.find_elements(By.CSS_SELECTOR, "div.error.vs2")) > 0 or
                              len(d.find_elements(By.CSS_SELECTOR, "div.content h1")) > 0
                )
            except:
                pass

        aggressive_popup_killer(inst)

        try:
            error_div = inst.driver.find_element(By.CSS_SELECTOR, "div.error.vs2")
            error_text = error_div.text.strip()
            if "not found" in error_text.lower():
                logger.warning(f"⚠️ [inst-{inst.id}] Part not found: {error_text}")
                return {
                    "product": {"part_number": "NOT FOUND", "description": error_text},
                    "pricing": {"price": None},
                    "details": {"from_date": None, "to_date": None, "weight": None},
                    "compatibility": {"vehicle_count": 0, "first_vehicle_tags": None},
                }
        except:
            pass

        try:
            part_number = inst.driver.find_element(By.CSS_SELECTOR, "div.content h1").text
            description = inst.driver.find_element(By.CSS_SELECTOR, "div.content h2").text
        except:
            logger.error(f"❌ [inst-{inst.id}] Content failed to load")
            return {"success": False, "error": "Content failed to load (timeout or popup blocking)"}

        logger.info(f"✅ [inst-{inst.id}] Part: {part_number} — {description}")

        part_details = {}
        try:
            dt_elements = inst.driver.find_elements(By.CSS_SELECTOR, "div.content dl dt")
            dd_elements = inst.driver.find_elements(By.CSS_SELECTOR, "div.content dl dd")
            for dt, dd in zip(dt_elements, dd_elements):
                key = dt.text.replace(":", "").strip()
                value = dd.text.strip() if dd.text.strip() else "-"
                part_details[key] = value
        except Exception as e:
            logger.warning(f"⚠️ [inst-{inst.id}] Error extracting details: {str(e)[:50]}")

        vehicle_links_list = []
        try:
            links = inst.driver.find_elements(By.CSS_SELECTOR, "div.partSearchResults ul li a")
            for link in links:
                vehicle_links_list.append({"text": link.text, "url": link.get_attribute("href")})
            logger.info(f"✅ [inst-{inst.id}] Found {len(vehicle_links_list)} vehicles")
        except Exception as e:
            logger.warning(f"⚠️ [inst-{inst.id}] Error extracting vehicle links: {str(e)[:50]}")

        vehicle_tags = ""
        if vehicle_links_list:
            first_vehicle_url = vehicle_links_list[0]["url"]
            logger.info(f"🚗 [inst-{inst.id}] Navigating to first vehicle...")
            safe_navigate_realoem(inst, first_vehicle_url)
            time.sleep(0.5)
            aggressive_popup_killer(inst)
            try:
                results_section = WebDriverWait(inst.driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "partSearchResults"))
                )
                first_li = results_section.find_element(By.CSS_SELECTOR, "ul li:first-child")
                full_text = first_li.text.strip()
                vehicle_tags = full_text.split(':')[0].strip() if ':' in full_text else full_text
                logger.info(f"✅ [inst-{inst.id}] Vehicle tags: {vehicle_tags}")
            except Exception as e:
                logger.warning(f"⚠️ [inst-{inst.id}] Error extracting vehicle tags: {str(e)[:50]}")
                vehicle_tags = "N/A"

        return {
            "product": {"part_number": part_number, "description": description},
            "pricing": {"price": part_details.get("Price") or None},
            "details": {
                "from_date": part_details.get("From") or None,
                "to_date": part_details.get("To") or None,
                "weight": part_details.get("Weight") or None,
            },
            "compatibility": {
                "vehicle_count": len(vehicle_links_list),
                "first_vehicle_tags": vehicle_tags or None,
            },
        }

    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e).split('\n')[0][:200]
        logger.error(f"❌ [inst-{inst.id}] Error scraping RealOEM: {error_type}: {error_msg}")
        return {"success": False, "error": f"{error_type}: {error_msg}"}


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
        "pool": pool.status(),
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
        raise HTTPException(status_code=400, detail="Barcode cannot be empty")
    if scraper not in ["autodoc", "realoem"]:
        raise HTTPException(status_code=400, detail="Scraper must be 'autodoc' or 'realoem'")

    logger.info(f"🎯 Request: barcode={barcode} scraper={scraper.upper()}")
    timeout = REALOEM_TIMEOUT if scraper == "realoem" else AUTODOC_TIMEOUT

    # Acquire a browser instance (waits up to 30 s for one to become free)
    try:
        inst = pool.acquire(timeout=30)
    except RuntimeError:
        logger.warning(f"⚠️  All {POOL_MAX_INSTANCES} browser instances busy — rejecting {barcode}")
        raise HTTPException(status_code=503, detail="All scrapers busy, please retry in a moment")

    # Watchdog timer — kills the driver if the scrape exceeds the timeout.
    # This avoids the old two-thread race where the executor thread was
    # still using the driver while the main thread called revive().
    timed_out = threading.Event()

    def _watchdog():
        timed_out.set()
        logger.error(f"⏱️  [inst-{inst.id}] Scrape timed out after {timeout}s — killing driver")
        inst.quit()

    timer = threading.Timer(timeout, _watchdog)
    timer.start()
    try:
        result = _run_scrape(inst, barcode, scraper)
        timer.cancel()
        # If _run_scrape returned an error AND the watchdog caused it, give
        # a clearer timeout message.  If it returned success, keep the result
        # even if the watchdog fired a split-second later.
        if not result.success and timed_out.is_set():
            return ProductResponse(success=False, barcode=barcode, scraper=scraper,
                                   error=f"Scrape timed out after {timeout}s")
        return result
    except Exception as e:
        timer.cancel()
        if timed_out.is_set():
            return ProductResponse(success=False, barcode=barcode, scraper=scraper,
                                   error=f"Scrape timed out after {timeout}s")
        logger.error(f"❌ Error processing {barcode}: {str(e)}")
        logger.exception(e)
        return ProductResponse(success=False, barcode=barcode, scraper=scraper, error=str(e))
    finally:
        pool.release(inst)


def _run_scrape(inst: BrowserInstance, barcode: str, scraper: str) -> ProductResponse:
    """Core scrape logic — runs in the same thread as scrape_barcode()."""
    try:
        if scraper == "realoem":
            result = scrape_realoem_barcode(inst, barcode)
            if result.get("success") == False or result.get("error"):
                return ProductResponse(success=False, barcode=barcode, scraper=scraper,
                                       error=result.get("error", "Failed to scrape RealOEM"))
            logger.info(f"✅ [inst-{inst.id}] RealOEM scrape complete")
            return ProductResponse(success=True, barcode=barcode, scraper=scraper, data=result)

        else:  # autodoc
            inst.warmup_autodoc()  # no-op if already done

            url = f"https://www.autodoc.co.uk/spares-search?keyword={barcode}"
            logger.info(f"🌐 [inst-{inst.id}] Navigating to: {url}")
            inst.driver.get(url)
            inst.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            _wait_for_cloudflare(inst, max_wait=60)
            _handle_cookies(inst)
            time.sleep(3)

            product_link = get_first_product_link(inst, barcode)
            if not product_link:
                return ProductResponse(success=False, barcode=barcode, scraper=scraper,
                                       error="No product found for this barcode")

            logger.info(f"✅ [inst-{inst.id}] Product link: {product_link}")
            product_data = scrape_product_details(inst, product_link, barcode)
            logger.info(f"✅ [inst-{inst.id}] Autodoc scrape complete")
            return ProductResponse(success=True, barcode=barcode, scraper=scraper, data=product_data)

    except Exception as e:
        logger.error(f"❌ [inst-{inst.id}] _run_scrape error for {barcode}: {str(e)}")
        logger.exception(e)
        return ProductResponse(success=False, barcode=barcode, scraper=scraper, error=str(e))


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
    uvicorn.run(app, host="0.0.0.0", port=8010)
