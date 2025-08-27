import os
from playwright.sync_api import sync_playwright
import time
from datetime import datetime, timedelta
import re


def parse_event_date(text):
    """Extrae fecha/hora de un texto de tarjeta de evento."""
    if not text:
        return None

    # Caso 1: con hora
    match = re.search(r"([A-Za-z]+, [A-Za-z]+ \d{1,2}, \d{4}) at (\d{1,2}:\d{2})", text)
    if match:
        try:
            return datetime.strptime(
                f"{match.group(1)} {match.group(2)}", "%A, %B %d, %Y %H:%M"
            )
        except Exception:
            pass

    # Caso 2: solo fecha sin hora
    match = re.search(r"([A-Za-z]+, [A-Za-z]+ \d{1,2}, \d{4})", text)
    if match:
        try:
            return datetime.strptime(match.group(1), "%A, %B %d, %Y")
        except Exception:
            pass

    return None


def find_input(page, selectors):
    """Try multiple selectors and return the first matching input element."""
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=5000)
            return sel
        except Exception:
            continue
    return None


def parse_event_info(card_text):
    """
    Extrae fecha, título, ubicación y stats de un texto de tarjeta de evento.
    Ejemplo de entrada:
    'Dim, 14 sept. à 15:00 CEST\n[HDE CSU Webinar 1] Extraction Planning\nEn ligne\n2 intéressés · 1 participants'
    """
    lines = card_text.split("\n")
    date_line = ""
    title_line = ""
    location_line = ""
    interested = None
    participants = None

    # Buscar línea de fecha (francés, inglés, etc.)
    date_pattern = r"^(Dim|Lun|Mar|Mer|Jeu|Ven|Sam|Sun|Mon|Tue|Wed|Thu|Fri|Sat)[^\n]*\d{1,2} [a-zéû\.]+ à \d{1,2}:\d{2}"
    for i, line in enumerate(lines):
        if re.match(date_pattern, line, re.IGNORECASE):
            date_line = line
            # Siguiente línea suele ser título
            if i + 1 < len(lines):
                title_line = lines[i + 1]
            # Ubicación suele estar después del título
            if i + 2 < len(lines):
                location_line = lines[i + 2]
            break

    # Stats (interesados/participantes)
    stats_pattern = r"(\d+)\s+intéressés.*?(\d+)\s+participants"
    for line in lines:
        m = re.search(stats_pattern, line)
        if m:
            interested = int(m.group(1))
            participants = int(m.group(2))
            break

    # Parsear fecha francesa
    event_date = None
    if date_line:
        # Ejemplo: 'Dim, 14 sept. à 15:00 CEST'
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
        # Reemplazar mes francés por inglés
        for fr, en in fr_months.items():
            date_line = date_line.replace(fr, en)
        # Eliminar día de la semana
        date_line = re.sub(
            r"^(Dim|Lun|Mar|Mer|Jeu|Ven|Sam|Sun|Mon|Tue|Wed|Thu|Fri|Sat),?\s*",
            "",
            date_line,
            flags=re.IGNORECASE,
        )
        # Parsear
        m = re.search(r"(\d{1,2}) ([A-Za-z]+) à (\d{1,2}):(\d{2})", date_line)
        if m:
            day = int(m.group(1))
            month = m.group(2)
            hour = int(m.group(3))
            minute = int(m.group(4))
            year = datetime.now().year
            try:
                event_date = datetime.strptime(
                    f"{day} {month} {year} {hour}:{minute}", "%d %b %Y %H:%M"
                )
            except Exception:
                event_date = None

    return {
        "fecha": date_line,
        "fecha_parseada": event_date.isoformat() if event_date else None,
        "titulo": title_line.strip() if title_line else "",
        "ubicacion": location_line.strip() if location_line else "",
        "interesados": interested,
        "participantes": participants,
    }


def ensure_fb_login_state():
    """Ensure Facebook login state is saved in state.json."""
    state_path = "state.json"
    if not os.path.exists(state_path):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto("https://www.facebook.com/login")
            print("👉 Please log in manually...")
            page.wait_for_timeout(120000)  # 180s to log in
            context.storage_state(path=state_path)
            browser.close()
        print("✅ Login state saved to state.json")
    else:
        print("✅ state.json found, using saved login state.")


def scrape_facebook_events(ciudad, palabra_clave="", start_date=None, end_date=None):
    url = f"https://www.facebook.com/events/search/?q={palabra_clave}"
    eventos = []
    state_path = "state.json"
    ensure_fb_login_state()
    with sync_playwright() as p:
        if os.path.exists(state_path):
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=state_path)
        else:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
        page = context.new_page()
        page.goto(url, timeout=60000)

        # Esperar a que carguen resultados
        page.wait_for_selector("a[href*='/events/']", timeout=15000)

        # Buscar ciudad (location input)
        location_selectors = [
            "input[placeholder*='Location']",
            "input[placeholder*='Ubicación']",
            "input[placeholder*='Lieu']",
            "input[aria-label*='Location']",
            "input[aria-label*='Ubicación']",
            "input[aria-label*='Lieu']",
        ]
        location_input_sel = find_input(page, location_selectors)
        if location_input_sel:
            page.fill(location_input_sel, ciudad)
            time.sleep(2)
            # Intentar abrir el dropdown manualmente si no aparece
            page.keyboard.press("ArrowDown")
            time.sleep(1)
            # Esperar el dropdown del combobox y seleccionar la primera sugerencia
            try:
                dropdown_selector = "ul[role='listbox'] li, div[role='option']"
                page.wait_for_selector(dropdown_selector, timeout=5000)
                suggestions = page.query_selector_all(dropdown_selector)
                if suggestions:
                    # Buscar la primera sugerencia visible y clicarla
                    for s in suggestions:
                        if s.is_visible():
                            s.click()
                            print(f"✅ Sugerencia seleccionada: {s.inner_text()}")
                            time.sleep(2)
                            break
                    else:
                        print("⚠️ Ninguna sugerencia visible encontrada")
                else:
                    print("⚠️ No se encontraron sugerencias en el combobox")
            except Exception as ex:
                print(f"⚠️ No se pudo seleccionar la sugerencia de ubicación: {ex}")
            page.keyboard.press("Enter")
            time.sleep(3)
        else:
            print("⚠️ No se encontró el input de ubicación")

        # Scroll infinito
        prev_height = 0
        for _ in range(10):
            page.mouse.wheel(0, 3000)
            time.sleep(3)
            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == prev_height:
                break
            prev_height = new_height

        # Extraer eventos (evitar duplicados con un set)
        seen = set()
        links = page.query_selector_all("a[href*='/events/']")
        for link in links:
            href = link.get_attribute("href")
            if not href or "events" not in href:
                continue
            clean_href = href.split("?")[0]
            if clean_href in seen:
                continue
            seen.add(clean_href)

            # Obtener todo el texto relevante del card
            card_text = ""
            try:
                card_text = link.evaluate("node => node.closest('div').innerText")
            except Exception:
                pass

            info = parse_event_info(card_text)
            event_date = None
            if info["fecha_parseada"]:
                event_date = datetime.fromisoformat(info["fecha_parseada"])

            # Filtrar por fecha
            matches_date = False
            if start_date and end_date and event_date:
                matches_date = start_date <= event_date <= end_date

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

        browser.close()
    return eventos


# 🔹 Uso
if __name__ == "__main__":
    # próximos 30 días
    start = datetime.now()
    end = start + timedelta(days=30)

    eventos = scrape_facebook_events("paris", "concert", start_date=start, end_date=end)

    for e in eventos:
        print("📌", e["titulo"])
        print("🔗", e["link"])
        print("🗓️", e["fecha_parseada"])
        print("-" * 50)
