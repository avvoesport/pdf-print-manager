from playwright.sync_api import sync_playwright
import os
import re

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
    print("Finding a pdf...")
    page.wait_for_selector("div[role='button']", timeout=15000)
    
    pdfs = page.locator("div[role='button']").filter(has_text=re.compile(r"\.pdf", re.IGNORECASE))
    
    count = pdfs.count()
    print(f"Found {count} PDFs.")
    
    for i in range(min(3, count)):
        bubble = pdfs.nth(i)
        date_str = bubble.evaluate("""el => {
            let curr = el;
            while(curr && curr.getAttribute('role') !== 'row') {
                curr = curr.parentElement;
            }
            if (!curr) return null;
            let pre = curr.getAttribute('data-pre-plain-text');
            if (pre) {
                let m = pre.match(/\\[.*?, (.*?)\\]/);
                if (m) return m[1];
            }
            let prev = curr.previousElementSibling;
            while (prev) {
                if (prev.innerText && prev.innerText.length < 20) {
                    let text = prev.innerText.trim();
                    if (text === 'TODAY' || text === 'YESTERDAY' || text.match(/^\\d{2}\\/\\d{2}\\/\\d{4}$/) || text.match(/^[A-Za-z]+day$/)) {
                        return text;
                    }
                }
                prev = prev.previousElementSibling;
            }
            return null;
        }""")
        print(f"PDF {i} date_str: {date_str}")
        
    context.close()
