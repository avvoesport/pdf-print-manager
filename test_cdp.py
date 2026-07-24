from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://google.com")
    print("Page loaded.")
    
    try:
        client = context.new_cdp_session(page)
        info = client.send("Browser.getWindowForTarget")
        window_id = info["windowId"]
        client.send("Browser.setWindowBounds", {
            "windowId": window_id,
            "bounds": {"windowState": "normal", "left": -20000, "top": -20000}
        })
        print("Moved off-screen!")
    except Exception as e:
        print("CDP Error:", e)
        
    print("Trying to click something...")
    try:
        page.locator("a").first.click(timeout=2000)
        print("Click succeeded!")
    except Exception as e:
        print("Click failed:", e)
        
    time.sleep(1)
    browser.close()
