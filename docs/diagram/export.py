"""Exports architecture{,-dark}.html to docs/architecture{,-dark}.png (the diagram only, at 2x)."""
import pathlib, sys
from playwright.sync_api import sync_playwright

here = pathlib.Path(__file__).parent
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(device_scale_factor=int(sys.argv[1]) if len(sys.argv) > 1 else 2)
    for slug in ("architecture", "architecture-dark"):
        page.goto((here / f"{slug}.html").resolve().as_uri())
        page.wait_for_load_state("networkidle")
        page.evaluate("document.fonts.ready")
        page.locator("svg").first.screenshot(path=str(here.parent / f"{slug}.png"), omit_background=True)
        print(here.parent / f"{slug}.png")
    browser.close()
