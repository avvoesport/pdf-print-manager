from playwright.sync_api import sync_playwright
import os

WHATSAPP_PROFILE_DIR = os.path.abspath(os.path.join(os.path.expanduser("~"), "whatsapp_profile"))

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        WHATSAPP_PROFILE_DIR,
        headless=False,
        args=["--no-sandbox", "--window-size=1100,780"],
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://web.whatsapp.com", wait_until="networkidle")
    
    print("Waiting for chat list...")
    page.wait_for_selector("div[title='Alvin Ku']", timeout=30000)
    page.click("div[title='Alvin Ku']")
    print("Clicked Alvin Ku, scrolling...")
    
    page.keyboard.press("PageUp")
    page.keyboard.press("PageUp")
    page.keyboard.press("PageUp")
    page.wait_for_timeout(3000)
    
    pdfs = page.locator("div[role='button']").filter(has_text="pdf")
    count = pdfs.count()
    print(f"Found {count} PDFs.")
    
    if count > 0:
        first_pdf = pdfs.nth(0)
        html = first_pdf.evaluate("el => { let curr = el; while(curr && !curr.getAttribute('data-id')) { curr = curr.parentElement; } return curr ? curr.outerHTML : el.outerHTML; }")
        
        with open("wa_dump.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Dumped HTML to wa_dump.html")
    
    context.close()
