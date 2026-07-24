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
    
    # Use existing page to find a pdf
    print("Waiting for you to open Alvin Ku...")
    page.wait_for_selector("div[role='button']:has-text('.pdf')", timeout=60000)
    
    # find the first pdf
    bubble = page.locator("div[role='button']").filter(has_text=".pdf").first
    
    # execute JS to find data-pre-plain-text
    info = bubble.evaluate("""el => {
        let curr = el;
        while(curr && curr.getAttribute('role') !== 'row') {
            curr = curr.parentElement;
        }
        let result = {};
        if (curr) {
            result.prePlain = curr.getAttribute('data-pre-plain-text');
            result.html = curr.outerHTML.substring(0, 500); // just snippet
        }
        return result;
    }""")
    
    print("Date info found:", info)
    
    context.close()
