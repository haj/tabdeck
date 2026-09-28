"""Screenshots of the demo hub (docs/screenshots/demo.py) for the README: phone-sized, 3x."""
import pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).parent
URL = "http://127.0.0.1:8799/"


def shot(page, name):
    page.wait_for_timeout(600)
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(OUT / f"{name}.png")


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 390, "height": 760}, device_scale_factor=3, is_mobile=True,
                              has_touch=True, color_scheme="dark")
    page = ctx.new_page()
    page.goto(URL)
    page.wait_for_load_state("networkidle")
    shot(page, "sessions")

    page.locator("#list button", has_text="api").first.click()
    page.get_by_role("button", name="Show terminal").click()
    shot(page, "session")

    page.goto(URL)
    page.wait_for_load_state("networkidle")
    page.evaluate("localStorage.clear()")
    page.goto(URL)
    page.wait_for_load_state("networkidle")
    page.click("#new")
    shot(page, "new-session")

    page.goto(URL)
    page.wait_for_load_state("networkidle")
    page.click("#settings")
    shot(page, "settings")
    browser.close()
