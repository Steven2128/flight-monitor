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
    """Cierra banners de cookies/consent."""
    for text in ["Aceptar todo", "Accept all", "Agree", "I agree", "Aceptar"]:
        try:
            page.click(f"button:has-text('{text}')", timeout=2000)
            page.wait_for_timeout(1000)
            return
        except:
            pass

def force_cop_currency(page):
    """Cambia la moneda a COP desde el menú de Google Flights."""
    try:
        # Busca el botón de moneda (ej: "USD" o "$") y haz clic
        page.click("[aria-label*='Currency'], [aria-label*='Moneda'], button:has-text('USD')", timeout=4000)
        page.wait_for_timeout(1000)
        # Busca COP en el dropdown
        page.click("li:has-text('COP'), [data-value='COP'], span:has-text('Peso colombiano')", timeout=4000)
        page.wait_for_timeout(2000)
        print("    💱 Moneda cambiada a COP")
    except:
        pass  # si no aparece el selector, continuamos igual

def extract_prices(page, currency="COP"):
    """Extrae precios en la moneda dada."""
    cops = []
    is_cop = currency == "COP"
    min_val = 50_000   if is_cop else 30
    max_val = 5_000_000 if is_cop else 2_000

    # Estrategia 1: aria-label
    try:
        labels = page.eval_on_selector_all(
            "[aria-label]",
            "els => els.map(e => e.getAttribute('aria-label'))"
        )
        for lbl in labels:
            if lbl and (currency in lbl or "$" in lbl):
                for n in re.findall(r'[\d]+[.,\d]*[\d]', lbl):
                    val = float(n.replace(".", "").replace(",", ""))
                    if min_val < val < max_val:
                        cops.append(val)
    except:
        pass

    # Estrategia 2: texto visible
    if not cops:
        try:
            body = page.inner_text("body")
            pattern = r'\$\s*([\d]{2,3}(?:[.,]\d{3})+)' if is_cop else r'\$\s*(\d{2,4}(?:\.\d{2})?)'
            for m in re.findall(pattern, body):
                val = float(m.replace(".", "").replace(",", ""))
                if min_val < val < max_val:
                    cops.append(val)
        except:
            pass

    # Estrategia 3: HTML
    if not cops:
        try:
            html = page.content()
            for m in re.findall(r'(?:COP|"price":|>)\s*\$?\s*([\d]{3}(?:[.,]\d{3})+)', html):
                val = float(m.replace(".", "").replace(",", ""))
                if min_val < val < max_val:
                    cops.append(val)
        except:
            pass

    return cops

USD_TO_COP = 4_200  # tasa de cambio aproximada para conversión si es necesario

def get_cheapest_price(origin, destination, dep_date):
    # tt:o = solo ida | c:COP = pesos colombianos
    urls = [
        f"https://www.google.com.co/travel/flights?hl=es-419#flt={origin}.{destination}.{dep_date};c:COP;e:1;sd:1;t:f;tt:o",
        f"https://www.google.com/travel/flights?hl=es-419#flt={origin}.{destination}.{dep_date};c:COP;e:1;sd:1;t:f;tt:o",
    ]
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
            geolocation={"latitude": 4.711, "longitude": -74.0721},  # Bogotá
            permissions=["geolocation"],
            viewport={"width": 1280, "height": 800},
            extra_http_headers={
                "Accept-Language": "es-CO,es;q=0.9",
            },
        )

        # Cookie para forzar COP en Google
        ctx.add_cookies([{
            "name": "CURRENCY", "value": "COP",
            "domain": ".google.com", "path": "/"
        }, {
            "name": "CURRENCY", "value": "COP",
            "domain": ".google.com.co", "path": "/"
        }])

        page = ctx.new_page()
        price, details, currency_used = None, None, "COP"

        for url in urls:
            try:
                page.goto(url, wait_until="networkidle", timeout=40000)
                page.wait_for_timeout(3000)
                accept_cookies(page)
                force_cop_currency(page)     # ← intenta cambiar a COP desde UI
                page.wait_for_timeout(5000)

                # Intenta extraer en COP primero
                cops = extract_prices(page, "COP")

                # Si no hay COP, intenta USD y convierte
                if not cops:
                    usd_prices = extract_prices(page, "USD")
                    if usd_prices:
                        cops = [v * USD_TO_COP for v in usd_prices]
                        currency_used = "USD→COP"
                        print(f"    💱 Precios en USD convertidos a COP (x{USD_TO_COP})")

                if cops:
                    price   = min(cops)
                    details = {"source": "Google Flights", "url": url,
                               "currency": currency_used}
                    break

            except Exception as e:
                print(f"    ⚠️  Error: {e}")

        if price is None:
            try:
                page.screenshot(path=f"debug_{origin}_{destination}.png", full_page=True)
                print(f"    📸 Screenshot guardado para debug")
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
