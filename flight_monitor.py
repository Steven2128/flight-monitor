"""
✈️ COLOMBIA FLIGHT PRICE MONITOR
Google Flights scraping con Playwright
Diseñado para correr una vez por ejecución (GitHub Actions lo dispara cada 30 min)
"""

import re
import json
import requests
import os
from datetime import datetime, date
from playwright.sync_api import sync_playwright

# ============================================================
# 🔧 CONFIGURACIÓN
# ============================================================

WHATSAPP_NUMBER  = "+573014091603"
CALLMEBOT_APIKEY = "9481713"

ROUTES = [
    {"origin": "BOG", "destination": "SMR", "date": "2026-06-08", "label": "Bogotá → Santa Marta"},
    {"origin": "SMR", "destination": "MDE", "date": "2026-06-11", "label": "Santa Marta → Medellín"},
    {"origin": "MDE", "destination": "BOG", "date": "2026-06-15", "label": "Medellín → Bogotá"},
]

STOP_DATE   = date(2026, 4, 30)
PRICES_FILE = "lowest_prices.json"

# ============================================================
# ✈️ SCRAPING GOOGLE FLIGHTS
# ============================================================

def accept_cookies(page):
    for text in ["Aceptar todo", "Accept all", "Agree", "I agree", "Aceptar"]:
        try:
            page.click(f"button:has-text('{text}')", timeout=2000)
            page.wait_for_timeout(800)
            return
        except:
            pass

def get_usd_to_cop():
    """Tasa de cambio USD→COP en tiempo real."""
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=USD&to=COP", timeout=5)
        rate = r.json()["rates"]["COP"]
        print(f"    💱 Tasa USD→COP: {rate:,.0f}")
        return rate
    except:
        return 4_200  # fallback

def fill_input(page, selectors, text):
    """Intenta llenar un input probando varios selectores."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            el.click(timeout=2000)
            page.wait_for_timeout(400)
            el.triple_click()
            page.keyboard.press("Control+a")
            page.keyboard.press("Delete")
            el.type(text, delay=100)
            page.wait_for_timeout(1500)
            # Seleccionar primera sugerencia del dropdown
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(400)
            page.keyboard.press("Enter")
            page.wait_for_timeout(800)
            return True
        except:
            continue
    return False

def extract_cop_prices(page):
    """Extrae precios en COP del texto visible."""
    cops = []
    try:
        body = page.inner_text("body")
        # Formato colombiano: 189.000 / 1.234.567
        for m in re.findall(r'\b(\d{1,3}(?:\.\d{3})+)\b', body):
            val = float(m.replace(".", ""))
            if 80_000 < val < 5_000_000:
                cops.append(val)
    except:
        pass
    return cops

def get_cheapest_price(origin, destination, dep_date):
    usd_to_cop = get_usd_to_cop()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"]
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-CO",
            timezone_id="America/Bogota",
            viewport={"width": 1280, "height": 800},
            extra_http_headers={"Accept-Language": "es-CO,es;q=0.9"},
        )
        page = ctx.new_page()
        price, details = None, None

        try:
            # 1. Ir a Google Flights con curr=COP
            page.goto(
                "https://www.google.com/travel/flights?hl=es-419&curr=COP",
                wait_until="networkidle", timeout=40000
            )
            page.wait_for_timeout(2000)
            accept_cookies(page)
            page.wait_for_timeout(1000)

            # 2. Cambiar a "Solo ida" — clic en el selector de tipo de viaje
            trip_selectors = [
                "div[jsname='UjMaPb']",
                "[aria-label*='viaje'], [aria-label*='trip type']",
                "div.VfPpkd-TkwUic",
            ]
            for sel in trip_selectors:
                try:
                    page.click(sel, timeout=2000)
                    page.wait_for_timeout(600)
                    break
                except:
                    continue
            for opt in ["Solo ida", "One way"]:
                try:
                    page.click(f"li:has-text('{opt}')", timeout=2000)
                    page.wait_for_timeout(500)
                    break
                except:
                    continue

            # 3. Origen — limpiar primero con tecla Escape y reabrir
            origin_sels = [
                "input[aria-label*='Origen']",
                "input[placeholder*='Origen']",
                "input[aria-label*='Where from']",
                "[data-placeholder*='Origen'] input",
            ]
            # Limpiar campo origen (puede tener valor previo)
            for sel in origin_sels:
                try:
                    page.click(sel, timeout=2000)
                    page.keyboard.press("Control+a")
                    page.keyboard.press("Delete")
                    page.wait_for_timeout(300)
                    break
                except:
                    continue
            filled = fill_input(page, origin_sels, origin)
            if not filled:
                raise Exception(f"No se pudo llenar origen: {origin}")

            # 4. Destino
            dest_sels = [
                "input[aria-label*='Destino']",
                "input[placeholder*='Destino']",
                "input[aria-label*='Where to']",
                "[data-placeholder*='Destino'] input",
            ]
            filled = fill_input(page, dest_sels, destination)
            if not filled:
                raise Exception(f"No se pudo llenar destino: {destination}")

            # 5. Fecha de salida
            date_sels = [
                "input[aria-label*='Salida']",
                "input[aria-label*='Departure']",
                "input[placeholder*='Ida']",
            ]
            for sel in date_sels:
                try:
                    page.click(sel, timeout=2000)
                    page.wait_for_timeout(800)
                    break
                except:
                    continue

            # Navegar hasta junio 2026 en el calendario
            for _ in range(14):  # max 14 meses
                try:
                    header = page.locator("h2").filter(has_text="2026").inner_text(timeout=1000)
                    if "jun" in header.lower():
                        break
                    page.click("button[aria-label*='siguiente'], button[aria-label*='Next']", timeout=2000)
                    page.wait_for_timeout(400)
                except:
                    break

            # Clic en el día exacto
            day = str(int(dep_date.split("-")[2]))  # "08" → "8"
            try:
                page.click(f"[data-iso='{dep_date}']", timeout=3000)
            except:
                try:
                    page.click(f"td[aria-label*='{day}'][aria-label*='junio']", timeout=3000)
                except:
                    pass
            page.wait_for_timeout(500)

            # Cerrar calendario
            for btn in ["Listo", "Done", "Aceptar"]:
                try:
                    page.click(f"button:has-text('{btn}')", timeout=2000)
                    break
                except:
                    continue
            page.wait_for_timeout(800)

            # 6. Buscar
            for btn in ["Buscar", "Search"]:
                try:
                    page.click(f"button:has-text('{btn}')", timeout=3000)
                    break
                except:
                    continue
            page.wait_for_timeout(7000)  # esperar resultados

            # 7. Screenshot de resultados para verificar
            page.screenshot(path=f"result_{origin}_{destination}.png")

            # 8. Extraer precios COP
            cops = extract_cop_prices(page)

            # Fallback: si sigue en USD, convertir con tasa real
            if not cops:
                body = page.inner_text("body")
                usd_vals = []
                for m in re.findall(r'(?<!\d)\$\s*(\d{2,4})(?!\d)', body):
                    val = float(m)
                    if 20 < val < 3000:
                        usd_vals.append(val)
                if usd_vals:
                    cops = [v * usd_to_cop for v in usd_vals]
                    print(f"    💱 Convertido {len(usd_vals)} precios USD→COP")

            if cops:
                price   = min(cops)
                details = {"source": "Google Flights", "url": page.url}

        except Exception as e:
            print(f"    ⚠️  Error: {e}")
            try:
                page.screenshot(path=f"debug_{origin}_{destination}.png", full_page=True)
            except:
                pass

        browser.close()
    return price, details

# ============================================================
# 📲 WHATSAPP (CallMeBot)
# ============================================================

def send_whatsapp(message):
    try:
        r = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": WHATSAPP_NUMBER, "text": message, "apikey": CALLMEBOT_APIKEY},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"    ⚠️  WhatsApp error: {e}")
        return False

# ============================================================
# 💾 GUARDAR/CARGAR MÍNIMOS
# ============================================================

def load_prices():
    if os.path.exists(PRICES_FILE):
        with open(PRICES_FILE) as f:
            return json.load(f)
    return {}

def save_prices(data):
    with open(PRICES_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ============================================================
# 🔁 CHEQUEO PRINCIPAL (corre una sola vez)
# ============================================================

def check_prices():
    # Asegura que el archivo siempre existe (evita error en git)
    if not os.path.exists(PRICES_FILE):
        save_prices({})

    if date.today() > STOP_DATE:
        print("✅ Monitoreo terminado (pasó el 30 de abril).")
        return

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Revisando precios...")
    records = load_prices()

    for route in ROUTES:
        key = f"{route['origin']}-{route['destination']}"
        print(f"  🔍 {route['label']}...", end=" ", flush=True)

        price, details = get_cheapest_price(
            route["origin"], route["destination"], route["date"]
        )

        if price is None:
            print("sin resultados.")
            continue

        prev      = records.get(key, {}).get("price")
        fmt_price = f"$ {price:,.0f} COP"
        fmt_prev  = f"$ {prev:,.0f} COP" if prev else "ninguno aún"
        print(f"{fmt_price}  (mínimo anterior: {fmt_prev})")

        if prev is None or price < prev:
            records[key] = {
                "price":    price,
                "details":  details,
                "found_at": str(datetime.now()),
            }
            save_prices(records)

            msg = (
                f"🚨 *NUEVO PRECIO MÍNIMO!*\n"
                f"✈️ {route['label']}\n"
                f"📅 Vuelo: {route['date']}\n"
                f"💰 {fmt_price}\n"
                f"📉 Anterior mínimo: {fmt_prev}\n"
                f"🔗 {details['url']}\n"
                f"👉 ¡Compra antes de que suba!"
            )
            ok = send_whatsapp(msg)
            print(f"     → 🆕 NUEVO MÍNIMO | WhatsApp: {'✅ enviado' if ok else '⚠️ falló'}")

check_prices()
