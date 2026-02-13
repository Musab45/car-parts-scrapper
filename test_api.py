"""
Test script for the Auto Parts Scraper API
"""
import requests
import json

# API base URL
BASE_URL = "http://localhost:8000"

def test_root():
    """Test root endpoint"""
    print("=" * 60)
    print("Testing Root Endpoint")
    print("=" * 60)
    response = requests.get(f"{BASE_URL}/")
    print(f"Status: {response.status_code}")
    print(json.dumps(response.json(), indent=2))
    print()

def test_health():
    """Test health check"""
    print("=" * 60)
    print("Testing Health Check")
    print("=" * 60)
    response = requests.get(f"{BASE_URL}/health")
    print(f"Status: {response.status_code}")
    print(json.dumps(response.json(), indent=2))
    print()

def test_realoem_scraper(barcode="34116860912"):
    """Test RealOEM scraper with POST method"""
    print("=" * 60)
    print(f"Testing RealOEM Scraper (POST) - Barcode: {barcode}")
    print("=" * 60)
    
    payload = {
        "barcode": barcode,
        "scraper": "realoem"
    }
    
    response = requests.post(f"{BASE_URL}/scrape", json=payload)
    print(f"Status: {response.status_code}")
    result = response.json()
    print(json.dumps(result, indent=2))
    print()
    
    return result

def test_realoem_scraper_get(barcode="34116860912"):
    """Test RealOEM scraper with GET method"""
    print("=" * 60)
    print(f"Testing RealOEM Scraper (GET) - Barcode: {barcode}")
    print("=" * 60)
    
    response = requests.get(f"{BASE_URL}/scrape/{barcode}?scraper=realoem")
    print(f"Status: {response.status_code}")
    result = response.json()
    print(json.dumps(result, indent=2))
    print()
    
    return result

def test_autodoc_scraper(barcode="34116860912"):
    """Test Autodoc scraper with POST method"""
    print("=" * 60)
    print(f"Testing Autodoc Scraper (POST) - Barcode: {barcode}")
    print("=" * 60)
    
    payload = {
        "barcode": barcode,
        "scraper": "autodoc"
    }
    
    response = requests.post(f"{BASE_URL}/scrape", json=payload)
    print(f"Status: {response.status_code}")
    result = response.json()
    print(json.dumps(result, indent=2))
    print()
    
    return result

if __name__ == "__main__":
    try:
        # Test basic endpoints
        test_root()
        test_health()
        
        # Test RealOEM scraper
        print("\n🚀 Testing RealOEM Scraper\n")
        test_realoem_scraper("34116860912")
        
        # Test with GET method
        # test_realoem_scraper_get("34116860912")
        
        # Test Autodoc scraper (optional)
        # print("\n🚀 Testing Autodoc Scraper\n")
        # test_autodoc_scraper("some_barcode")
        
    except requests.exceptions.ConnectionError:
        print("❌ Error: Could not connect to API")
        print("Make sure the API is running: uvicorn app:app --reload")
    except Exception as e:
        print(f"❌ Error: {e}")
