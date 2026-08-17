"""Visit the Streamlit Cloud app in a headless browser so it registers as
real activity (a plain HTTP ping doesn't - see .github/workflows/keep-alive.yml).
"""

from playwright.sync_api import sync_playwright

APP_URL = "https://select-fantasy-football-auction.streamlit.app/"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(APP_URL, wait_until="networkidle", timeout=60_000)

        wake_button = page.get_by_text("get this app back up", exact=False)
        if wake_button.count() > 0:
            wake_button.first.click()
            page.wait_for_timeout(15_000)

        page.wait_for_timeout(5_000)
        browser.close()


if __name__ == "__main__":
    main()
