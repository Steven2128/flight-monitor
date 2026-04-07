"""
✈️ COLOMBIA FLIGHT PRICE MONITOR
Scraping Google Flights con Playwright — sin registro, sin API keys

Rutas:
  BOG → SMR  |  8 junio 2026
  SMR → MDE  |  11 junio 2026
  MDE → BOG  |  15 junio 2026

Corre cada 30 min hasta el 30 de abril de 2026
"""

import re
import json
import time
import schedule
import requests
import os
from datetime import datetime, date
from playwright.sync_api import sync_playwright

# ============================================================
# 🔧 CONFIGURACIÓN — Solo llena estos dos campos
# ============================================================

WHATSAPP_NUMBER  = "+573014091603"
CALLMEBOT_APIKEY = "9481713"

# ============================================================
# 🗺️ RUTAS (no tocar)
# ============================================================

ROUTES = [
    {"origin": "BOG", "destination": "SMR", "date": "2026-06-08", "label": "Bogotá → Santa Marta"},
    {"origin": "SMR", "destination": "MDE", "date": "2026-06-11", "label": "Santa Marta → Medellín"},
    {"origin": "MDE", "destination": "BOG", "date": "2026-06-15", "label": "Medellín → Bogotá"},
]

STOP_DATE   = date(2026, 4, 30)
PRICES_FILE = "lowest_prices.json"
CHECK_EVERY = 30  # minutos (sin límite de API, sin problema)

# ============================================================
# ✈️ SCRAPING GOOGLE FLIGHTS
# ============================================================

def get_cheapest_price(origin, destination, dep_date):
    """Abre Google Flights en modo headless y extrae el precio mínimo en COP."""
    url = (
        f"https://www.google.com/travel/flights?hl=es-419"
        f"#flt={origin}.{destination}.{dep_date};c:COP;e:1;sd:1;t:f"
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx  = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-CO",
        )
        page = ctx.new_page()
        price, details = None, None

        try:
            page.goto(url, wait_until="networkidle", timeout=40000)
            page.wait_for_timeout(5000)  # espera que cargue el JS

            # ── Intentar con aria-label primero (más preciso) ──
            labels = page.eval_on_selector_all(
                "[aria-label]",
                "els => els.map(e => e.getAttribute('aria-label'))"
            )
            cops = []
            for lbl in labels:
                if lbl and ("COP" in lbl or "$" in lbl):
                    nums = re.findall(r'[\d]+[.\d]*[\d]', lbl)
                    for n in nums:
                        val = float(n.replace(".", "").replace(",", ""))
                        if 50_000 < val < 5_000_000:
                            cops.append(val)

            # ── Fallback: texto completo de la página ──
            if not cops:
                body = page.inner_text("body")
                matches = re.findall(r'\$\s*([\d]{2,3}(?:[.,]\d{3})+)', body)
                for m in matches:
                    val = float(m.replace(".", "").replace(",", ""))
                    if 50_000 < val < 5_000_000:
                        cops.append(val)

            if cops:
                price   = min(cops)
                details = {"source": "Google Flights", "url": url}

        except Exception as e:
            print(f"    ⚠️  Playwright error: {e}")
        finally:
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
# 🔁 CHEQUEO PRINCIPAL
# ============================================================

def check_prices():
    if date.today() > STOP_DATE:
        print("✅ Monitoreo terminado (pasó el 30 de abril). Apagando.")
        raise SystemExit(0)

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Revisando precios...")
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
                f"🔗 Google Flights: {details['url']}\n"
                f"👉 ¡Compra antes de que suba!"
            )
            ok = send_whatsapp(msg)
            print(f"     → 🆕 NUEVO MÍNIMO | WhatsApp: {'✅ enviado' if ok else '⚠️ falló'}")

# ============================================================
# 🚀 INICIO
# ============================================================

print("🛫 Monitor de vuelos iniciado!")
print(f"   Revisando cada {CHECK_EVERY} min hasta {STOP_DATE}\n")

check_prices()  # corre inmediatamente al arrancar
schedule.every(CHECK_EVERY).minutes.do(check_prices)

while True:
    schedule.run_pending()
    time.sleep(30)
