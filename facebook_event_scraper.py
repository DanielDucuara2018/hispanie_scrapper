import os
import re
import time
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright


class FacebookEventScraper:
    def __init__(self, headless=True, state_path="state.json"):
        self.headless = headless
        self.state_path = state_path
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
                page.goto("https://www.facebook.com/login")
                print("👉 Please log in manually...")
                page.wait_for_timeout(120000)  # tiempo para loguearse
                context.storage_state(path=self.state_path)
                browser.close()
            print("✅ Login state saved to", self.state_path)
        else:
            print("✅ state.json found, using saved login state.")

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

    def _parse_event_info(self, card_text):
        """Extrae fecha, título, ubicación y stats de un texto de tarjeta de evento."""
        lines = card_text.split("\n")
        date_line, title_line, location_line = "", "", ""
        interested, participants = None, None

        # fecha
        date_pattern = r"^(Dim|Lun|Mar|Mer|Jeu|Ven|Sam|Sun|Mon|Tue|Wed|Thu|Fri|Sat)[^\n]*\d{1,2} [a-zéû\.]+ à \d{1,2}:\d{2}"
        for i, line in enumerate(lines):
            if re.match(date_pattern, line, re.IGNORECASE):
                date_line = line
                if i + 1 < len(lines):
                    title_line = lines[i + 1]
                if i + 2 < len(lines):
                    location_line = lines[i + 2]
                break

        # stats
        stats_pattern = r"(\d+)\s+intéressés.*?(\d+)\s+participants"
        for line in lines:
            m = re.search(stats_pattern, line)
            if m:
                interested = int(m.group(1))
                participants = int(m.group(2))
                break

        # fecha parseada (fr → en)
        event_date = None
        if date_line:
            fr_months = {
                "janv.": "Jan",
                "févr.": "Feb",
                "mars": "Mar",
                "avr.": "Apr",
                "mai": "May",
                "juin": "Jun",
                "juil.": "Jul",
                "août": "Aug",
                "sept.": "Sep",
                "oct.": "Oct",
                "nov.": "Nov",
                "déc.": "Dec",
            }
            for fr, en in fr_months.items():
                date_line = date_line.replace(fr, en)
            date_line = re.sub(
                r"^(Dim|Lun|Mar|Mer|Jeu|Ven|Sam|Sun|Mon|Tue|Wed|Thu|Fri|Sat),?\s*",
                "",
                date_line,
                flags=re.IGNORECASE,
            )
            m = re.search(r"(\d{1,2}) ([A-Za-z]+) à (\d{1,2}):(\d{2})", date_line)
            if m:
                try:
                    day, month, hour, minute = (
                        int(m.group(1)),
                        m.group(2),
                        int(m.group(3)),
                        int(m.group(4)),
                    )
                    year = datetime.now().year
                    event_date = datetime.strptime(
                        f"{day} {month} {year} {hour}:{minute}", "%d %b %Y %H:%M"
                    )
                except Exception:
                    pass

        return {
            "fecha": date_line,
            "fecha_parseada": event_date.isoformat() if event_date else None,
            "titulo": title_line.strip() if title_line else "",
            "ubicacion": location_line.strip() if location_line else "",
            "interesados": interested,
            "participantes": participants,
        }

    # ---------------------------
    # 🔹 Búsqueda de eventos
    # ---------------------------
    def scrape(self, ciudad, palabra_clave="", start_date=None, end_date=None):
        url = f"https://www.facebook.com/events/search/?q={palabra_clave}"
        self.page.goto(url, timeout=60000)
        # self.page.wait_for_selector("a[href*='/events/']", timeout=15000)

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
            time.sleep(2)
            self.page.keyboard.press("ArrowDown")
            time.sleep(1)
            try:
                dropdown_selector = "ul[role='listbox'] li, div[role='option']"
                self.page.wait_for_selector(dropdown_selector, timeout=5000)
                suggestions = self.page.query_selector_all(dropdown_selector)
                for s in suggestions:
                    if s.is_visible():
                        s.click()
                        time.sleep(2)
                        break
            except Exception:
                pass
            self.page.keyboard.press("Enter")
            time.sleep(3)

        # scroll infinito
        prev_height = 0
        for _ in range(10):
            self.page.mouse.wheel(0, 3000)
            time.sleep(3)
            new_height = self.page.evaluate("document.body.scrollHeight")
            if new_height == prev_height:
                break
            prev_height = new_height

        # extraer eventos
        eventos, seen = [], set()
        links = self.page.query_selector_all("a[href*='/events/']")
        for link in links:
            href = link.get_attribute("href")
            if not href or "events" not in href:
                continue
            clean_href = href.split("?")[0]
            if clean_href in seen:
                continue
            seen.add(clean_href)

            try:
                card_text = link.evaluate("node => node.closest('div').innerText")
            except Exception:
                card_text = ""
            info = self._parse_event_info(card_text)
            event_date = (
                datetime.fromisoformat(info["fecha_parseada"])
                if info["fecha_parseada"]
                else None
            )

            matches_date = not (start_date and end_date) or (
                event_date and start_date <= event_date <= end_date
            )
            if matches_date:
                eventos.append(
                    {
                        "titulo": info["titulo"],
                        "link": clean_href,
                        "fecha": info["fecha"],
                        "fecha_parseada": info["fecha_parseada"],
                        "ubicacion": info["ubicacion"],
                        "interesados": info["interesados"],
                        "participantes": info["participantes"],
                    }
                )
        return eventos

    def scrape_multiple(self, ciudad, palabras_claves, start_date=None, end_date=None):
        """Ejecuta varias búsquedas con una lista de palabras clave."""
        all_results = {}
        for palabra in palabras_claves:
            print(f"🔍 Buscando: {palabra}")
            eventos = self.scrape(ciudad, palabra, start_date, end_date)
            all_results[palabra] = eventos
        return all_results


# ---------------------------
# 🔹 Ejemplo de uso con context manager
# ---------------------------
if __name__ == "__main__":
    start = datetime.now()
    end = start + timedelta(days=7)

    with FacebookEventScraper(headless=True) as scraper:
        resultados = scraper.scrape_multiple(
            "Nantes",
            ["salsa", "SBK", "bacchata", "baile"],
            start_date=start,
            end_date=end,
        )

    for palabra, eventos in resultados.items():
        print(f"\n=== {palabra.upper()} ===")
        for e in eventos:
            print("📌", e["titulo"])
            print("🔗", e["link"])
            print("🗓️", e["fecha_parseada"])
            print("-" * 50)
