from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com")
    print("Page loaded. Now minimizing...")
    
    try:
        client = context.new_cdp_session(page)
        info = client.send("Browser.getWindowForTarget")
        window_id = info["windowId"]
        client.send("Browser.setWindowBounds", {
            "windowId": window_id,
            "bounds": {"windowState": "minimized"}
        })
        print("Minimized!")
    except Exception as e:
        print("CDP Error:", e)
        
    time.sleep(1)
    browser.close()
