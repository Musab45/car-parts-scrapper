import requests
import base64
import time

CAPSOLVER_API_KEY = 'CAP-B5AF60896E5D3460063F4148663FAD01'

def download_captcha_image(image_url):
    response = requests.get(image_url)
    if response.status_code == 200:
        with open("debug_captcha.png", "wb") as f:
            f.write(response.content)
            print("[DEBUG] Saved captcha as debug_captcha.png")
        return base64.b64encode(response.content).decode('utf-8')
    else:
        raise Exception(f"Failed to download image: {response.status_code}")

# Step 2: Submit CAPTCHA to CapSolver
def submit_captcha(base64_image):
    url = "https://api.capsolver.com/createTask"
    payload = {
        "clientKey": CAPSOLVER_API_KEY,
        "task": {
            "type": "ImageToTextTask",
            "body": base64_image,
            "module": "default",
            "case": "mixed",
            "recognizingThreshold": 0.3
        }
            }
    response = requests.post(url, json=payload).json()
    if 'taskId' not in response:
        raise Exception(f"Error from CapSolver: {response}")
    print(response['taskId'])
    return response['taskId']

# Step 3: Get result from CapSolver
def get_captcha_result(task_id):
    url = "https://api.capsolver.com/getTaskResult"
    payload = {
        "clientKey": CAPSOLVER_API_KEY,
        "taskId": task_id
    }
    while True:
        response = requests.post(url, json=payload).json()
        if response.get('status') == 'ready':
            return response['solution']['text']
        time.sleep(2)  # Wait before retrying
        
def check_capsolver_balance():
    url = "https://api.capsolver.com/getBalance"
    payload = {
        "clientKey": CAPSOLVER_API_KEY
    }

    response = requests.post(url, json=payload)
    data = response.json()
    
    if 'balance' in data:
        print(f"✅ API key is valid. Balance: ${data['balance']}")
    else:
        print(f"❌ Invalid key or error: {data}")
    
def dynamic_captcha(sitekey, url):
    payload = {
            "clientKey": CAPSOLVER_API_KEY,
            "task": {
                "type": "AntiTurnstileTaskProxyLess",
                "websiteKey": sitekey,
                "websiteURL": url,
                "metadata": {
                    "action": "",  # optional
                }
            }
        }
    # Step 1: Send the captcha to CapSolver
    res = requests.post("https://api.capsolver.com/createTask", json=payload)
    resp = res.json()
    print(resp)
    task_id = resp.get("taskId")
    
    # Step 2: Wait for the captcha to be solved
    while True:
        time.sleep(5)  # Wait a few seconds before checking again
        payload = {"clientKey": CAPSOLVER_API_KEY, "taskId": task_id}
        res = requests.post("https://api.capsolver.com/getTaskResult", json=payload)
        resp = res.json()
        print("==========================")
        print(resp)
        print("==========================")
        status = resp.get("status")
        
        if status == 'ready':
            return resp.get("solution", {}).get('token')
        
        if status == 'failed':
            raise Exception("Failed to solve captcha: " + resp.get('errorCode'))

def solve_captcha_from_base64(img_base64):
    if not img_base64 or len(img_base64) < 50:
        raise Exception("Captured CAPTCHA image is empty or too small — likely a CORS or load issue.")
    
    create_task_payload = {
        "clientKey": CAPSOLVER_API_KEY,
        "task": {
            "type": "ImageToTextTask",
            "body": img_base64
        }
    }

    create_task_response = requests.post(
        "https://api.capsolver.com/createTask",
        json=create_task_payload
    ).json()

    if create_task_response.get("errorId") != 0:
        raise Exception(f"Error creating task: {create_task_response}")

    task_id = create_task_response["taskId"]

    # Poll until result is ready, max 10 tries
    for attempt in range(10):
        result = requests.post(
            "https://api.capsolver.com/getTaskResult",
            json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id}
        ).json()

        if result.get("status") == "ready":
            return result["solution"]["text"]

        time.sleep(1)  # Wait 1 second before retrying

    raise Exception("CAPTCHA solving timed out after 10 attempts")

def solve_captcha_from_file(file_path):
    with open(file_path, "rb") as f:
        img_base64 = base64.b64encode(f.read()).decode("utf-8")
    print(type(img_base64))

    payload = {
        "clientKey": CAPSOLVER_API_KEY,
        "task": {
            "type": "ImageToTextTask",
            "module":"module_030",
            "body": img_base64
        }
    }
    create_task = requests.post("https://api.capsolver.com/createTask", json=payload).json()
    if create_task.get("errorId") != 0:
        raise Exception(f"Error creating task: {create_task}")

    task_id = create_task["taskId"]

    for _ in range(20):
        result = requests.post("https://api.capsolver.com/getTaskResult", json={
            "clientKey": CAPSOLVER_API_KEY,
            "taskId": task_id
        }).json()
        if result.get("status") == "ready":
            return result["solution"]["text"]
        time.sleep(1)

    raise Exception("CAPTCHA solving timed out")