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
    """Cierra banners de cookies/consent que bloquean el scraping."""
    for text in ["Aceptar todo", "Accept all", "Agree", "I agree", "Aceptar"]:
        try:
            page.click(f"button:has-text('{text}')", timeout=2000)
            page.wait_for_timeout(1000)
            return
        except:
            pass

def extract_prices(page):
    """Extrae precios COP de la página por múltiples estrategias."""
    cops = []

    # Estrategia 1: aria-label (más preciso)
    try:
        labels = page.eval_on_selector_all(
            "[aria-label]",
            "els => els.map(e => e.getAttribute('aria-label'))"
        )
        for lbl in labels:
            if lbl and ("COP" in lbl or "$" in lbl):
                for n in re.findall(r'[\d]+[.\d]*[\d]', lbl):
                    val = float(n.replace(".", "").replace(",", ""))
                    if 50_000 < val < 5_000_000:
                        cops.append(val)
    except:
        pass

    # Estrategia 2: texto visible de la página
    if not cops:
        try:
            body = page.inner_text("body")
            for m in re.findall(r'\$\s*([\d]{2,3}(?:[.,]\d{3})+)', body):
                val = float(m.replace(".", "").replace(",", ""))
                if 50_000 < val < 5_000_000:
                    cops.append(val)
        except:
            pass

    # Estrategia 3: HTML completo
    if not cops:
        try:
            html = page.content()
            for m in re.findall(r'(?:COP|"price":|>)\s*\$?\s*([\d]{3}(?:[.,]\d{3})+)', html):
                val = float(m.replace(".", "").replace(",", ""))
                if 50_000 < val < 5_000_000:
                    cops.append(val)
        except:
            pass

    return cops

def get_cheapest_price(origin, destination, dep_date):
    urls = [
        f"https://www.google.com/travel/flights?hl=es-419#flt={origin}.{destination}.{dep_date};c:COP;e:1;sd:1;t:f",
        f"https://www.google.com/travel/flights?hl=es-419&q=vuelos+{origin}+{destination}+{dep_date}",
    ]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-CO",
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        price, details = None, None

        for url in urls:
            try:
                page.goto(url, wait_until="networkidle", timeout=40000)
                page.wait_for_timeout(3000)
                accept_cookies(page)          # ← cierra banner de cookies
                page.wait_for_timeout(5000)   # espera que carguen los vuelos

                cops = extract_prices(page)
                if cops:
                    price   = min(cops)
                    details = {"source": "Google Flights", "url": url}
                    break   # encontrado, no necesitamos el segundo URL

            except Exception as e:
                print(f"    ⚠️  Error ({url[:50]}...): {e}")

        # Guarda screenshot si no encuentra precios (útil para debug)
        if price is None:
            try:
                page.screenshot(path=f"debug_{origin}_{destination}.png")
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