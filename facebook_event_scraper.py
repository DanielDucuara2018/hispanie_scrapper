import os
import re
import time
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
from typing import Any
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


DATE_FMT = "%Y-%m-%d"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


# ---------------------------
# 🔹 Email sender
# ---------------------------
def send_events_email(
    eventos: dict[str, list[dict[str, Any]]],
    city: str,
    start_date: datetime,
    end_date: datetime,
    sender_email,
    password,
    recipient_emails,
    smtp_server="smtp.gmail.com",
    smtp_port=587,
):
    """Send events via email using SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"📅 Facebook Events Report {city.capitalize()}. From {start_date.strftime(DATE_FMT)} to {end_date.strftime(DATE_FMT)}"
    )
    msg["From"] = sender_email
    msg["To"] = ", ".join(recipient_emails)

    # build HTML body
    html = "<h1>Facebook Events</h1>"
    for eventos in eventos.values():
        for e in eventos:
            html += f"<li><b>{e['titulo']}</b> - {e['fecha']} - {e['ubicacion']}<br>"
            html += f"<a href='{e['link']}'>🔗 Event Link</a></li>"
        html += "</ul>"

    msg.attach(MIMEText(html, "html"))

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
    - TODO this case 'du 18 déc. 20:00 au 22 déc. 03:00'
    Returns: (start_datetime, end_datetime)
    """
    if ref_date is None:
        ref_date = datetime.now()

    date_text = date_text.strip().lower()
    start_dt, end_dt = None, None

    # Extract times
    time_match = re.search(r"(\d{1,2}:\d{2})\s*à\s*(\d{1,2}:\d{2})", date_text)
    if not time_match:
        return None, None
    start_time_str, end_time_str = time_match.groups()

    # Default event date = today
    event_date = ref_date.date()

    # Case 1: "Demain ..."
    if "demain" in date_text:
        event_date = (ref_date + timedelta(days=1)).date()

    # Case 2: "samedi 11 avril 2026"
    full_date_match = re.search(
        r"(\d{1,2})\s+([a-zéû\.]+)\s+(\d{4})", date_text, re.IGNORECASE
    )
    if full_date_match:
        day, month_fr, year = full_date_match.groups()
        fr_months = {
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
        }
        month_en = fr_months.get(month_fr.strip("."), month_fr)
        try:
            event_date = datetime.strptime(
                f"{day} {month_en} {year}", "%d %B %Y"
            ).date()
        except Exception:
            pass

    # Case 3: only weekday (samedi, dimanche, etc.)
    else:
        weekdays = {
            "lundi": 0,
            "mardi": 1,
            "mercredi": 2,
            "jeudi": 3,
            "vendredi": 4,
            "samedi": 5,
            "dimanche": 6,
        }
        for wd_name, wd_num in weekdays.items():
            if wd_name in date_text:
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

    # Handle overnight
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    return start_dt, end_dt


class FacebookEventScraper:
    def __init__(self, url, headless=True, state_path="state.json"):
        self.url = url
        self.headless = headless
        self.state_path = state_path
        self.seen = set()
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    # ---------------------------
    # 🔹 Context Manager
    # ---------------------------
    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    # ---------------------------
    # 🔹 Gestión del navegador
    # ---------------------------
    def _ensure_login_state(self):
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

    def open(self):
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

    def close(self):
        """Cierra navegador y playwright."""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    # ---------------------------
    # 🔹 Utilidades
    # ---------------------------
    def _find_input(self, selectors):
        """Try multiple selectors and return the first matching input element."""
        for sel in selectors:
            try:
                self.page.wait_for_selector(sel, timeout=5000)
                return sel
            except Exception:
                continue
        return None

    def _parse_event_page(self, event_url: str, keyword: str, city: str) -> dict | None:
        """Visita la página del evento y extrae fecha, título, ubicación y stats."""
        try:
            logger.info("🔗 Visiting event page: %s", event_url)
            self.page.goto(event_url, timeout=60000)
        except Exception:
            logger.error("❌ Error visiting event page")
            return None

        # título
        try:
            title = (
                self.page.query_selector(f"span:has-text('{keyword}')")
                .inner_text()
                .strip()
            )
        except Exception:
            logger.error("❌ Error extracting event title")
            title = ""

        # fecha
        try:
            date_line = ""
            blocks = self.page.query_selector_all("div[role='button'][tabindex='0']")
            for block in blocks:
                spans = block.query_selector_all("span[dir='auto']")
                for s in spans:
                    text = s.inner_text().strip()
                    if re.search(
                        r"\d{1,2}:\d{2}\s*à\s*\d{1,2}:\d{2}", text
                    ):  # pattern "20:00 à 01:30"
                        date_line = text
                        break
        except Exception:
            logger.error("❌ Error extracting event date")
            date_line = ""

        # ubicación
        city = city.capitalize()
        try:
            location = (
                self.page.query_selector(
                    f"span:has-text('à {city} ({city})'), span:has-text('{city}, France')"
                )
                .inner_text()
                .strip()
            )
        except Exception:
            logger.error("❌ Error extracting event location")
            location = ""

        start_dt, end_dt = "", ""
        if date_line:
            start_dt, end_dt = parse_event_date(date_line)
            date_line = f"from {start_dt.strftime(DATETIME_FORMAT)} to {end_dt.strftime(DATETIME_FORMAT)}"

        extracted_info = {
            "fecha": date_line,
            "start_dt": start_dt,
            "end_dt": end_dt,
            "titulo": title,
            "ubicacion": location,
        }
        logger.info("✅ Event parsed: %s", extracted_info)
        return extracted_info

    # ---------------------------
    # 🔹 Búsqueda de eventos
    # ---------------------------
    def scrape(self, ciudad, palabra_clave="", start_date=None, end_date=None):
        url = f"{self.url}/events/search/?q={palabra_clave}"
        self.page.goto(url, timeout=60000)

        # insertar ciudad
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
            self.page.fill(location_input_sel, ciudad)
            time.sleep(1)
            self.page.keyboard.press("ArrowDown")
            time.sleep(1)
            try:
                dropdown_selector = "ul[role='listbox'] li, div[role='option']"
                self.page.wait_for_selector(dropdown_selector, timeout=5000)
                suggestions = self.page.query_selector_all(dropdown_selector)
                for s in suggestions:
                    if s.is_visible():
                        s.click()
                        time.sleep(1)
                        break
            except Exception:
                pass
            self.page.keyboard.press("Enter")
            time.sleep(1)

        # scroll infinito
        prev_height = 0
        for _ in range(10):
            self.page.mouse.wheel(0, 3000)
            time.sleep(1)
            new_height = self.page.evaluate("document.body.scrollHeight")
            if new_height == prev_height:
                break
            prev_height = new_height

        # extraer eventos
        eventos = []
        hrefs = [
            link.get_attribute("href")
            for link in self.page.query_selector_all("a[href*='/events/']")
        ]
        for href in hrefs:
            if not href or "events" not in href or self.url in href:
                continue
            clean_href = href.split("?")[0]
            if clean_href in self.seen:
                continue
            self.seen.add(clean_href)

            # ahora visitamos la página del evento
            info = self._parse_event_page(self.url + clean_href, palabra_clave, ciudad)
            if not info or not all(info.values()):
                continue

            start_dt = info["start_dt"]
            end_dt = info["end_dt"]
            matches_date = not (start_date and end_date) or (
                start_dt and start_date <= start_dt <= end_date
            )
            if matches_date:
                eventos.append(
                    {
                        "titulo": info["titulo"],
                        "link": self.url + clean_href,
                        "fecha": info["fecha"],
                        "start_dt": start_dt,
                        "end_dt": end_dt,
                        "ubicacion": info["ubicacion"],
                    }
                )
        return eventos

    def scrape_multiple(self, ciudad, palabras_claves, start_date=None, end_date=None):
        """Ejecuta varias búsquedas con una lista de palabras clave."""
        all_results = {}
        for palabra in palabras_claves:
            logger.info("🔍 Buscando: %s", palabra)
            eventos = self.scrape(ciudad, palabra, start_date, end_date)
            all_results[palabra] = eventos
        return all_results


# ---------------------------
# 🔹 Ejemplo de uso con context manager
# ---------------------------
if __name__ == "__main__":
    start = datetime.now()
    end = start + timedelta(days=7)
    city = "paris"

    with FacebookEventScraper("https://www.facebook.com", headless=True) as scraper:
        resultados = scraper.scrape_multiple(
            city,
            [
                "Son Cubain",
                "erasmus",
                "Panama",
                "Garifuna",
                "colombie",
                "Ranchera",
                "Pasillo",
                "Tango",
                "salsa",
                "bresil",
                "Venezuela",
                "kizomba",
                "Mambo",
                "Punta",
                "Candombe",
                "tejido",
                "SBK",
                "hispanique",
                "Joropo",
                "Panama",
                "Colombie",
                "Peru",
                "forro",
                "Republica Dominicana",
                "Nicaragua",
                "Murga",
                "bailar",
                "Guanacasteco",
                "Bolivia",
                "Spain",
                "Ecuador",
                "Zuliana",
                "Chota",
                "latin",
                "cueca",
                "Colombia",
                "Argentina",
                "Español",
                "Corrido",
                "Chile",
                "champeta",
                "Perou",
                "arepa",
                "Peru",
                "Dominican Republic",
                "Tamborito",
                "Rumba",
                "Bolivie",
                "Criolla",
                "bachata",
                "empanada",
                "flamenco",
                "cumbia",
                "fiesta",
                "Guatemala",
                "Cuban Son",
                "samba",
                "Porto Rico",
                "El Salvador",
                "Hispano",
                "funk",
                "perou",
                "Mexique",
                "Mexico",
                "Chapin",
                "Regueton",
                "currulao",
                "Son Cubano",
                "reggaeton",
                "merengue",
                "folclor",
                "criollo",
                "latina",
                "Paraguay",
                "Espagnol",
                "Uruguay",
                "Costa Rica",
                "Mariachi",
                "Banda",
                "carioca",
                "Folclore",
                "latino",
                "vallenato",
                "Brasil",
                "bossa",
                "Puerto Rico",
                "Marinera",
                "Chili",
                "Dembow",
                "Salvador",
                "España",
                "Festejo",
                "Spanish",
                "forro",
                "Argentine",
                "Hispanic",
                "Mexico",
                "Espagne",
                "Xuc",
                "caporales",
                "Bachata",
                "Llanera",
                "Honduras",
                "Cuba",
                "villera",
                "Equateur",
                "baile",
                "Republique dominicaine",
            ],
            start_date=start,
            end_date=end,
        )

    for eventos in resultados.values():
        for e in eventos:
            logger.info("📌 %s", e["titulo"])
            logger.info("🔗 %s", e["link"])
            logger.info("🗓️ %s", e["fecha"])
            logger.info("🗓️ %s", e["start_dt"])
            logger.info("🗓️ %s", e["end_dt"])
            logger.info("-" * 50)

    # send results by email
    send_events_email(
        resultados,
        city,
        start,
        end,
        sender_email="youremail@gmail.com",
        password="yourpassword",
        recipient_emails=["target@example.com"],
    )
