"""Capture fresh app screenshots for the walkthrough `shot` scenes.

Tours the running app (http://127.0.0.1:8765) tab by tab and grabs a crisp
2x screenshot of each into assets/shots/. Run with the app already up:

    python capture_shots.py
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765"
OUT = Path(__file__).resolve().parent / "assets" / "shots"
OUT.mkdir(parents=True, exist_ok=True)


def shot(page, name: str, settle: int = 1500):
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(settle)
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"  captured {name}.png")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-color-profile=srgb"])
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900}, device_scale_factor=2,
        )
        page = ctx.new_page()
        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(2500)  # let entrance animations settle

        # Deck is the default landing view
        shot(page, "deck", settle=2000)

        for tab, name, settle in [
            ("Queue", "queue", 1800),
            ("Intel", "intel", 2400),     # charts animate in
            ("Answers", "answers", 1600),
        ]:
            page.locator("nav button", has_text=tab).first.click()
            shot(page, name, settle=settle)

        # Settings sheet (gear icon)
        page.locator('button[title="Run settings"]').click()
        shot(page, "settings", settle=1400)

        ctx.close()
        browser.close()
    print("done.")


if __name__ == "__main__":
    main()
