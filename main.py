import os
import re
import time
import smtplib
import logging
import json
from itertools import chain
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
from playwright._impl._element_handle import ElementHandle
from typing import Any
from pathlib import Path
import locale

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger(__name__)


# Ensure French weekday/month parsing works
try:
    locale.setlocale(locale.LC_TIME, "fr_FR.UTF-8")
except Exception:
    pass  # fallback if system doesn't have French locale


BASE_DIR = Path(__file__).parent
OUTPUT_FOLDER = BASE_DIR.joinpath("output")

DATE_KEYWORDS = [
    "date",
    "dates",
    "fecha",
    "fechas",
    "jour",
    "jours",
    "datum",
]  # add languages as needed
DATE_FMT = "%Y-%m-%d"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
OUTPUT_FILE_DATETIME_FORMAT = datetime.now().strftime("%Y%m%d_%H%M%S")

FR_MONTHS = {
    "janvier": "january",
    "février": "february",
    "mars": "march",
    "avril": "april",
    "mai": "may",
    "juin": "june",
    "juillet": "july",
    "août": "august",
    "septembre": "september",
    "octobre": "october",
    "novembre": "november",
    "décembre": "december",
    "janv": "january",
    "févr": "february",
    "avr": "april",
    "juil": "july",
    "sept": "september",
    "oct": "october",
    "nov": "november",
    "déc": "december",
    "mar": "march",
}

FR_WEEKDAY = {
    "monday": "lundi",
    "tuesday": "mardi",
    "wednesday": "mercredi",
    "thursday": "jeudi",
    "friday": "vendredi",
    "saturday": "samedi",
    "sunday": "dimanche",
}

WEEKDAYS = {
    "demain": -1,
    "lundi": 0,
    "mardi": 1,
    "mercredi": 2,
    "jeudi": 3,
    "vendredi": 4,
    "samedi": 5,
    "dimanche": 6,
}

REGEX_TIME_CASE = r"(\d{1,2}:\d{2})\s*à\s*(\d{1,2}:\d{2})"
REGEX_DATE_CASE_2 = r"(?:[a-zéû\.]+)\s+de\s+(\d{1,2}:\d{2})\s+à\s+(\d{1,2}:\d{2})"
REGEX_DATE_CASE_3 = r"(\d{1,2})\s+([a-zéû\.]+)\s+(\d{4})"
REGEX_DATE_CASE_4 = r"(?:[a-zéû\.]+)?\s*(\d{1,2}) ([a-zéû\.]+) (\d{4}) à (\d{2}:\d{2})"
REGEX_DATE_CASE_5 = r"du (\d{1,2}) ([a-zéû\.]+)\.? (\d{2}:\d{2}) au (\d{1,2}) ([a-zéû\.]+)\.? (\d{2}:\d{2})"
REGEX_DATE_CASE_6 = r"(" + "|".join(WEEKDAYS.keys()) + r")\s*à\s*(\d{1,2}:\d{2})"


def default(date: datetime | Any) -> str | Any:
    if isinstance(date, datetime):
        return date.strftime(DATETIME_FORMAT)
    return date


def save_events_to_json(
    eventos: list[dict[str, Any]],
    city: str,
    output_folder: Path = OUTPUT_FOLDER,
) -> Path:
    """Save events to a JSON file and return the file path."""
    output_folder.mkdir(parents=True, exist_ok=True)
    file_path = output_folder.joinpath(
        f"events_{city}_{OUTPUT_FILE_DATETIME_FORMAT}.json"
    )
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(eventos, f, ensure_ascii=False, indent=4, default=default)
    logger.info("✅ Events saved to JSON file: %s", file_path)
    return file_path


# ---------------------------
# 🔹 Email sender
# ---------------------------
def send_events_email(
    events: list[dict[str, Any]],
    city: str,
    start_date: datetime,
    end_date: datetime,
    sender_email: str,
    password: str,
    recipient_emails: list[str],
    smtp_server: str = "smtp.gmail.com",
    smtp_port: int = 587,
):
    """Send events via email using SMTP."""
    # Save events to JSON file
    json_file_path = save_events_to_json(events, city)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"📅 Facebook Events Report {city.capitalize()}. From {start_date.strftime(DATE_FMT)} to {end_date.strftime(DATE_FMT)}"
    )
    msg["From"] = sender_email
    msg["To"] = ", ".join(recipient_emails)

    # build HTML body
    html = "<h1>Facebook Events</h1>"
    for e in events:
        html += f"<li><b>{e['title']}</b> - {e['date']} - {e['location']}<br>"
        html += f"<a href='{e['link']}'>🔗 Event Link</a></li>"
    html += "</ul>"

    msg.attach(MIMEText(html, "html"))

    # Attach the JSON file
    with open(json_file_path, "rb") as f:
        attachment = MIMEText(f.read(), "base64", "utf-8")
        attachment.add_header(
            "Content-Disposition", "attachment", filename=f"events_{city}.json"
        )
        msg.attach(attachment)

    # send email
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(sender_email, password)
        server.sendmail(sender_email, recipient_emails, msg.as_string())
    logger.info("✅ Events sent to: %s", recipient_emails)


# TODO fix this method. Start date and end date are wrong
def parse_event_date(date_text: str, ref_date: datetime = None):
    """
    Parse event date formats like:
    - 'Demain de 19:00 à 23:00'
    - 'samedi de 20:00 à 01:30'
    - 'Samedi 11 avril 2026 de 20:00 à 01:30'
    - 'Vendredi 19 septembre 2025 à 21:00'
    - 'du 18 déc. 20:00 au 22 déc. 03:00'
    - 'mercredi à 20:00' and 'demain à 20:00'
    Returns: (start_datetime, end_datetime)
    """
    if ref_date is None:
        ref_date = datetime.now()

    date_text = date_text.strip().lower()
    start_dt, end_dt = None, None

    # Case 5: du 18 déc. 20:00 au 22 déc. 03:00
    range_match = re.search(REGEX_DATE_CASE_5, date_text)
    if range_match:
        day1, month1, time1, day2, month2, time2 = range_match.groups()
        month1_en = FR_MONTHS.get(month1.strip("."), month1)
        month2_en = FR_MONTHS.get(month2.strip("."), month2)
        year = ref_date.year
        # Infer year for start and end dates
        try:
            start_dt = datetime.strptime(
                f"{day1} {month1_en} {year} {time1}", "%d %B %Y %H:%M"
            )
            end_dt = datetime.strptime(
                f"{day2} {month2_en} {year} {time2}", "%d %B %Y %H:%M"
            )
            # If end date is before start, increment year for end date
            if end_dt < start_dt:
                end_dt = end_dt.replace(year=year + 1)
            # If start date is before today, increment year for start date
            if start_dt < ref_date:
                start_dt = start_dt.replace(year=year + 1)
        except Exception:
            logger.error(f"❌ Error parsing date range case 5: {date_text}")
            return None, None
        return start_dt, end_dt

    # Case 4: Vendredi 19 septembre 2025 à 21:00
    single_match = re.search(REGEX_DATE_CASE_4, date_text)
    if single_match:
        day, month, year, time_str = single_match.groups()
        month_en = FR_MONTHS.get(month.strip("."), month)
        try:
            start_dt = datetime.strptime(
                f"{day} {month_en} {year} {time_str}", "%d %B %Y %H:%M"
            )
            end_dt = start_dt
        except Exception:
            logger.error(f"❌ Error parsing single date case 4: {date_text}")
            return None, None
        return start_dt, end_dt

    # Case 6: Handle "demain à HH:MM" and "<weekday> à HH:MM"
    match = re.search(REGEX_DATE_CASE_6, date_text)
    if match:
        wd_name, time_str = match.groups()
        if wd_name == "demain":
            event_date = (ref_date + timedelta(days=1)).date()
        else:
            wd_num = WEEKDAYS[wd_name]
            days_ahead = (wd_num - ref_date.weekday()) % 7
            event_date = (ref_date + timedelta(days=days_ahead)).date()
        hour, minute = map(int, time_str.split(":"))
        start_dt = datetime.combine(event_date, datetime.min.time()).replace(
            hour=hour, minute=minute
        )
        end_dt = start_dt
        return start_dt, end_dt

    # Extract times
    time_match = re.search(REGEX_TIME_CASE, date_text)
    if not time_match:
        return None, None
    start_time_str, end_time_str = time_match.groups()

    # Default event date = today
    event_date = ref_date.date()

    # Case 1: "Demain ..."
    if "demain" in date_text:
        event_date = (ref_date + timedelta(days=1)).date()

    # Case 2: "samedi 11 avril 2026"
    full_date_match = re.search(REGEX_DATE_CASE_3, date_text)
    if full_date_match:
        day, month_fr, year = full_date_match.groups()
        month_en = FR_MONTHS.get(month_fr.strip("."), month_fr)
        try:
            event_date = datetime.strptime(
                f"{day} {month_en} {year}", "%d %B %Y"
            ).date()
        except Exception:
            logger.error(f"❌ Error parsing full date case 2: {date_text}")
            pass
    # Case 3: only weekday (samedi, dimanche, etc.) or "mercredi de 23:00 à 05:00"
    else:
        for wd_name, wd_num in WEEKDAYS.items():
            if wd_name in date_text and wd_num > 0:
                days_ahead = (wd_num - ref_date.weekday()) % 7
                event_date = (ref_date + timedelta(days=days_ahead)).date()
                break

    # Parse start/end times
    start_hour, start_min = map(int, start_time_str.split(":"))
    end_hour, end_min = map(int, end_time_str.split(":"))

    start_dt = datetime.combine(event_date, datetime.min.time()).replace(
        hour=start_hour, minute=start_min
    )
    end_dt = datetime.combine(event_date, datetime.min.time()).replace(
        hour=end_hour, minute=end_min
    )

    # Handle overnight (end time is less than start time)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    return start_dt, end_dt


class FacebookEventScraper:
    def __init__(
        self, url: str, headless: bool = True, state_path: str | Path = "state.json"
    ):
        self.url: str = url
        self.headless: bool = headless
        self.state_path: str | Path = state_path
        self.seen: set[str] = set()
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    # ---------------------------
    # 🔹 Context Manager
    # ---------------------------
    def __enter__(self) -> "FacebookEventScraper":
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    # ---------------------------
    # 🔹 Gestión del navegador
    # ---------------------------
    def _ensure_login_state(self) -> None:
        """Ensure Facebook login state is saved in state.json."""
        if not os.path.exists(self.state_path):
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                context = browser.new_context()
                page = context.new_page()
                page.goto(f"{self.url}/login")
                logger.info("👉 Please log in manually...")
                page.wait_for_timeout(120000)  # tiempo para loguearse
                context.storage_state(path=self.state_path)
                browser.close()
            logger.info("✅ Login state saved to: %s", self.state_path)
        else:
            logger.info("✅ state.json found, using saved login state.")

    def open(self) -> None:
        """Abre el navegador con sesión guardada."""
        self._ensure_login_state()
        self.playwright = sync_playwright().start()
        if os.path.exists(self.state_path):
            self.browser = self.playwright.chromium.launch(headless=self.headless)
            self.context = self.browser.new_context(storage_state=self.state_path)
        else:
            self.browser = self.playwright.chromium.launch(headless=False)
            self.context = self.browser.new_context()
        self.page = self.context.new_page()

    def close(self) -> None:
        """Cierra navegador y playwright."""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    # ---------------------------
    # 🔹 Utilidades de navegación
    # ---------------------------
    def _goto_search_page(self, keyword: str) -> None:
        url = f"{self.url}/events/search/?q={keyword}"
        self.page.goto(url, timeout=60000)

    def _select_location(self, city: str) -> None:
        location_selectors = [
            "input[placeholder*='Location']",
            "input[placeholder*='Ubicación']",
            "input[placeholder*='Lieu']",
            "input[aria-label*='Location']",
            "input[aria-label*='Ubicación']",
            "input[aria-label*='Lieu']",
        ]
        location_input_sel = self._find_input(location_selectors)
        if location_input_sel:
            self.page.fill(location_input_sel, city)
            time.sleep(1)
            self.page.keyboard.press("ArrowDown")
            time.sleep(1)
            self.page.keyboard.press("Enter")
            logger.info(f"✅ Selected location option: {city}")
            time.sleep(1)
        else:
            logger.warning("⚠️ Location input not found")

    def _element_contains_date_text(self, el):
        try:
            text = el.inner_text() or ""
        except Exception:
            return False
        text = text.strip().lower()
        return any(k in text for k in DATE_KEYWORDS)

    def _find_dates_element(self):
        """
        Try multiple selectors / strategies to find the "Dates" filter element.
        Returns an element handle or None.
        """
        # 1) Some direct locators (case-insensitive regex text)
        try:
            # Playwright supports text=/.../ for regex matching
            loc = self.page.locator(
                "text=/\\b(dates?|date|fecha|fechas|jour|jours)\\b/i"
            )
            if loc.count() > 0:
                # pick the first visible one
                for i in range(loc.count()):
                    candidate = loc.nth(i)
                    if candidate.is_visible():
                        return candidate
        except Exception:
            pass

        # 2) Role-based tries
        candidates = [
            "button:has-text('Dates')",
            "button:has-text('Date')",
            "div[role='button']:has-text('Dates')",
            "div[role='button']:has-text('Date')",
            "div[role='button'][aria-label*='Date']",
            "div[role='button'][title*='Date']",
        ]
        for sel in candidates:
            try:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    return el
            except Exception:
                continue

        # 3) If there's a Filters button/panel, try to open it and then look again
        try:
            filters_loc = self.page.locator("text=/filters|filtrer|filtros|filtres/i")
            if filters_loc.count() > 0:
                for i in range(filters_loc.count()):
                    f = filters_loc.nth(i)
                    if f.is_visible():
                        f.click()
                        time.sleep(0.5)
                        # Try to find the dates element inside the now-open panel
                        el = self.page.query_selector(
                            "div[role='menu'] div:has-text('Dates'), div[role='menu'] div:has-text('Date')"
                        )
                        if el and el.is_visible():
                            return el
        except Exception:
            pass

        # 4) Fallback: iterate clickable elements and match text via Python
        try:
            clickable = self.page.query_selector_all(
                "div[role='button'], button, a[role='button']"
            )
            for el in clickable:
                try:
                    if not el.is_visible():
                        continue
                    if self._element_contains_date_text(el):
                        return el
                except Exception:
                    continue
        except Exception:
            pass

        # 5) Last-ditch XPath search for text nodes (case-insensitive)
        try:
            xpath = "//div[contains(translate(normalize-space(string(.)), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'dates') or contains(translate(normalize-space(string(.)), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'date') or contains(translate(normalize-space(string(.)), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'fecha')]"
            el = self.page.query_selector(f"xpath={xpath}")
            if el and el.is_visible():
                return el
        except Exception:
            pass

        return None

    def _select_date_filter(self, option: str) -> bool:
        """
        Click the Dates filter then select the option (e.g. 'Cette semaine').
        Returns True if option was clicked, False otherwise.
        """
        try:
            dates_el = self._find_dates_element()
            if not dates_el:
                logger.warning("⚠️ Could not locate Dates filter element")
                return False

            dates_el.click()
            # short wait for dropdown/menu to render
            try:
                self.page.wait_for_timeout(700)
            except Exception:
                time.sleep(0.7)

            # Look for the option inside an open menu/panel
            option_selector_variants = [
                f"div[role='menu'] div:has-text('{option}')",
                f"div[role='menu'] span:has-text('{option}')",
                f"text=/{re.escape(option)}/i",
                f"div:has-text('{option}')",
            ]

            for sel in option_selector_variants:
                try:
                    # wait a bit only when selector includes role=menu
                    if "role='menu'" in sel:
                        self.page.wait_for_selector(sel, timeout=3000)
                    opt = self.page.query_selector(sel)
                    if opt and opt.is_visible():
                        opt.click()
                        logger.info(f"✅ Selected date option: {option}")
                        return True
                except Exception:
                    continue

            # fallback: scan visible menu items after clicking
            try:
                menu_items = self.page.query_selector_all(
                    "div[role='menu'] div, div[role='menu'] li, div[role='menu'] a"
                )
                for mi in menu_items:
                    try:
                        text = (mi.inner_text() or "").strip()
                    except Exception:
                        text = ""
                    if text and option.lower() in text.lower():
                        mi.click()
                        logger.info(f"✅ Selected date option by fallback text: {text}")
                        return True
            except Exception:
                pass

            logger.warning(f"⚠️ Option '{option}' not found after opening Dates filter")
            return False

        except Exception as ex:
            logger.error(f"❌ Error selecting '{option}' filter: {ex}")
            return False

    def _scroll_events(self, scroll_count: int = 10, delay: int = 1) -> None:
        prev_height = 0
        for _ in range(scroll_count):
            self.page.mouse.wheel(0, 3000)
            time.sleep(delay)
            new_height = self.page.evaluate("document.body.scrollHeight")
            if new_height == prev_height:
                break
            prev_height = new_height

    def _extract_event_links(self) -> list[str]:
        hrefs = [
            link.get_attribute("href")
            for link in self.page.query_selector_all("a[href*='/events/']")
        ]
        event_links: list[str] = []
        for href in hrefs:
            if not href or "events" not in href or self.url in href:
                continue
            clean_href = href.split("?")[0]
            if clean_href in self.seen:
                continue
            self.seen.add(clean_href)
            event_links.append(clean_href)
        return event_links

        # ---------------------------

    # 🔹 Utilidades
    # ---------------------------
    def _find_input(self, selectors: list[str]) -> str | None:
        """Try multiple selectors and return the first matching input element."""
        for sel in selectors:
            try:
                self.page.wait_for_selector(sel, timeout=5000)
                return sel
            except Exception:
                continue
        return None

    @staticmethod
    def _get_event_title(blocks: list[ElementHandle], keyword: str) -> str:
        title = ""
        try:
            for block in blocks:
                spans = block.query_selector_all(f"span:has-text('{keyword}')")
                for s in spans:
                    title = s.inner_text().strip()
                    if title:
                        break
        except Exception:
            logger.error("❌ Error extracting event title")
        return title

    @staticmethod
    def _get_event_date(blocks: list[ElementHandle]) -> str:
        date = ""
        try:
            for block in blocks:
                spans = block.query_selector_all("span[dir='auto']")
                for s in spans:
                    text = s.inner_text().strip()
                    if any(
                        re.search(pattern, text, re.IGNORECASE)
                        for pattern in [
                            REGEX_DATE_CASE_2,
                            REGEX_DATE_CASE_3,
                            REGEX_DATE_CASE_4,
                            REGEX_DATE_CASE_5,
                        ]
                    ):
                        date = text
                        break
        except Exception:
            logger.error("❌ Error extracting event date")
        return date

    @staticmethod
    def _get_event_location(blocks: list[ElementHandle], city: str) -> str:
        city_cap = city.capitalize()
        location = ""
        try:
            for block in blocks:
                spans = block.query_selector_all(
                    f"span:has-text('à {city_cap} ({city_cap})'), span:has-text('{city_cap}, France')"
                )
                for s in spans:
                    location = s.inner_text().strip()
                    if location:
                        break
        except Exception:
            logger.error("❌ Error extracting event location")
        return location

    def _get_event_banner_image(self) -> str | None:
        """Extract the banner image link from the event page."""
        try:
            banner_img = self.page.query_selector(
                "img[data-imgperflogname='profileCoverPhoto']"
            )
            if banner_img:
                # Prefer highest resolution if available
                srcset = banner_img.get_attribute("srcset")
                if srcset:
                    # srcset looks like "url1 320w, url2 640w, url3 1280w"
                    urls = [s.strip().split(" ")[0] for s in srcset.split(",")]
                    return urls[-1]  # last one is usually highest res
                return banner_img.get_attribute("src")
        except Exception as ex:
            logger.error(f"❌ Error extracting banner image: {ex}")
        return None

    def _get_event_description(self) -> str:
        """Extract the event description (with fallback and 'See more' expansion)."""
        try:
            # Expand "See more" if present
            see_more = self.page.query_selector(
                "div[data-testid='event-permalink-details'] span:has-text('En voir plus')"
            )
            if see_more:
                see_more.click()
                time.sleep(1)

            # Try main selector
            description_block = self.page.query_selector(
                "div[data-testid='event-permalink-details']"
            )
            if description_block:
                return description_block.inner_text().strip()

            # Fallback: look for any block with role=article (sometimes used)
            fallback_block = self.page.query_selector("div[role='article']")
            if fallback_block:
                return fallback_block.inner_text().strip()

        except Exception as ex:
            logger.error(f"❌ Error extracting event description: {ex}")
        return ""

    def _parse_event_page(
        self, event_url: str, keyword: str, city: str
    ) -> dict[str, Any] | None:
        try:
            logger.info("🔗 Visiting event page: %s", event_url)
            self.page.goto(event_url, timeout=60000)
        except Exception:
            logger.error("❌ Error visiting event page")
            return None

        blocks = self.page.query_selector_all("div[role='button'][tabindex='0']")
        title = self._get_event_title(blocks, keyword)
        date_line = self._get_event_date(blocks)
        location = self._get_event_location(blocks, city)
        description = self._get_event_description()
        banner_image = self._get_event_banner_image()

        start_dt, end_dt = "", ""
        if date_line:
            start_dt, end_dt = parse_event_date(date_line)
            if start_dt and end_dt:
                weekday_fr = FR_WEEKDAY[start_dt.strftime("%A").lower()].capitalize()
                start_time = start_dt.strftime("%Hh%M")
                end_time = end_dt.strftime("%Hh%M")
                date_line = f"{weekday_fr} - {start_time} à {end_time}"

        extracted_info: dict[str, Any] = {
            "date": date_line,
            "start_dt": start_dt,
            "end_dt": end_dt,
            "title": title or "not found",
            "location": location or "not found",
            "description_short": description or "not found",
            "description_long": description or "not found",
            "image": banner_image or "not found",
            "link": event_url,
            "cost": "not found",
            "type": "not found",
        }
        logger.info("✅ Event parsed: %s", extracted_info)
        return extracted_info

    def _filter_event_by_date(
        self, info: dict[str, Any], start_date: datetime, end_date: datetime
    ) -> bool:
        start_dt = info["start_dt"]
        if not (start_date and end_date):
            return True
        return start_dt and start_date <= start_dt <= end_date

    # ---------------------------
    # 🔹 Búsqueda de eventos
    # ---------------------------
    def scrape(
        self,
        city: str,
        keyword: str = "",
        start_date: datetime = None,
        end_date: datetime = None,
    ) -> list[dict[str, Any]]:
        self._goto_search_page(keyword)
        self._select_location(city)
        self._select_date_filter("Cette semaine")
        self._scroll_events()
        event_links = self._extract_event_links()
        events = []
        for clean_href in event_links:
            info = self._parse_event_page(self.url + clean_href, keyword, city)
            if not info or not all(info.values()):
                continue
            if self._filter_event_by_date(info, start_date, end_date):
                events.append(info)
        return events

    def scrape_multiple(
        self,
        city: str,
        keywords: list[str],
        start_date: datetime = None,
        end_date: datetime = None,
    ) -> dict[str, list[dict[str, Any]]]:
        all_results = {}
        for keyword in keywords:
            logger.info("🔍 Searching: %s", keyword)
            events = self.scrape(city, keyword, start_date, end_date)
            all_results[keyword] = events
        return all_results


# ---------------------------
# 🔹 Ejemplo de uso con context manager
# ---------------------------
if __name__ == "__main__":
    start = datetime.now()
    end = start + timedelta(days=7)
    city = "paris"

    with FacebookEventScraper(
        "https://www.facebook.com",
        headless=True,
        state_path=BASE_DIR.joinpath("state.json"),
    ) as scraper:
        resultados = scraper.scrape_multiple(
            city,
            [
                "Uruguay",
                "Candombe",
                "Mariachi",
                "Spain",
                "Folclore",
                "Bolero",
                "Costa Rica",
                "Espagne",
                "Marinera",
                "onda libre",
                "Venezuela",
                "Bolivie",
                "frijol",
                "Guanacasteco",
                "fiesta",
                "folclor",
                "Colombia",
                "Chapin",
                "Espagnol",
                "Murga",
                "frijoles",
                "Argentin",
                "kizomba",
                "Brasil",
                "Argentina",
                "Puerto Rico",
                "perou",
                "Ecuador",
                "blabla",
                "cueca",
                "tacos",
                "caporales",
                "baile",
                "vallenato",
                "Latin",
                "hispano",
                "Porto Rico",
                "bachatero",
                "brésil",
                "Rumba",
                "latin",
                "funk",
                "Perou",
                "Llanera",
                "pupusas",
                "Equateur",
                "Español",
                "Mambo",
                "arepa",
                "Guatemala",
                "Xuc",
                "Punta",
                "Bachata",
                "Corrido",
                "Chile",
                "tropical",
                "champeta",
                "merengue",
                "Cuban Son",
                "mate",
                "Folklore",
                "Hispano",
                "Tamales",
                "mariachi",
                "Spanish",
                "reggaeton",
                "Republica Dominicana",
                "bla bla",
                "Nicaragua",
                "Criolla",
                "Republique dominicaine",
                "Banda",
                "El Salvador",
                "hispanique",
                "erasmus",
                "hispana",
                "Zumba",
                "Chili",
                "latino",
                "onda",
                "Dominican Republic",
                "Joropo",
                "colombie",
                "Salvador",
                "España",
                "empanada",
                "Regueton",
                "villera",
                "Peru",
                "taco",
                "Zuliana",
                "bailar",
                "Honduras",
                "Son Cubano",
                "salsero",
                "muertos",
                "Dembow",
                "bachata",
                "salsa",
                "Mexico",
                "Paraguay",
                "samba",
                "currulao",
                "latina",
                "Ranchera",
                "Cuba",
                "SBK",
                "Pasillo",
                "Garifuna",
                "carioca",
                "Son Cubain",
                "cumbia",
                "Hispanic",
                "Bolivia",
                "criollo",
                "Panama",
                "tejido",
                "bossa",
                "flamenco",
                "Tamborito",
                "Ceviche",
                "Agua",
                "Tango",
                "forro",
                "Brazil",
                "Mexique",
                "Chota",
                "Festejo",
                "bal",
                "bachat",
                "bachastyle",
                "zumasso",
                "latines",
                "latine",
                "palenque",
                "mexi",
                "mexic",
                "tropicalis",
                "caricombo",
                "urbanvoices",
            ],
            start_date=start,
            end_date=end,
        )

    events = list(chain.from_iterable(resultados.values()))
    events.sort(key=lambda x: x.get("start_dt", ""))
    for e in events:
        logger.info("📌 %s", e["title"])
        logger.info("🔗 %s", e["link"])
        logger.info("🗓️ %s", e["date"])
        logger.info("🗓️ %s", e["start_dt"])
        logger.info("🗓️ %s", e["end_dt"])
        logger.info("-" * 50)

    # send results by email
    send_events_email(
        events,
        city,
        start,
        end,
        sender_email="youremail@gmail.com",
        password="yourpassword",
        recipient_emails=["target@example.com"],
    )
