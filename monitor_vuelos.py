"""
✈️ COLOMBIA FLIGHT PRICE MONITOR
Google Flights scraping con Playwright
Diseñado para correr una vez por ejecución (GitHub Actions lo dispara cada 30 min)
"""

import re
import sys
import json
import requests
import os
from datetime import datetime, date
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

# ============================================================
# 🔧 CONFIGURACIÓN
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]

ROUTES      = json.loads(os.environ["ROUTES_JSON"])
STOP_DATE   = date.fromisoformat(os.environ["STOP_DATE"])
PRICES_FILE = os.environ.get("PRICES_FILE", "lowest_prices.json")

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


PRICE_RE = re.compile(r'COP\s*(\d{1,3}(?:[.,]\d{3})+)', re.IGNORECASE)

def extract_first_price_from_section(page):
    """Primer precio COP de 'Todos los vuelos' (ya ordenado por precio)."""
    try:
        body = page.inner_text("body")
        idx = body.find("Todos los vuelos")
        section = body[idx:idx + 2000] if idx >= 0 else body
        m = PRICE_RE.search(section)
        if m:
            val = float(m.group(1).replace(".", "").replace(",", ""))
            if 20_000 < val < 5_000_000:
                return val
    except:
        pass
    return None

def get_cheapest_price(origin, destination, dep_date):
    """Carga Google Flights: Más económicos → Ordenado por precio → primer precio."""
    from urllib.parse import quote
    q = quote(f"Flights from {origin} to {destination} on {dep_date} oneway")
    url = f"https://www.google.com/travel/flights?q={q}&curr=COP&hl=es-419"

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
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "es-CO,es;q=0.9"},
        )
        page = ctx.new_page()
        price, details = None, None

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            accept_cookies(page)
            page.wait_for_timeout(3000)

            # 1. Pestaña "Más económicos"
            try:
                page.click('text=Más económicos', timeout=8000)
                page.wait_for_timeout(4000)
            except:
                pass

            # 2. Abrir dropdown de orden
            try:
                page.click('text=Ordenado por vuelos principales', timeout=8000)
                page.wait_for_timeout(2000)
            except:
                pass

            # 3. Seleccionar "Precio" en el dropdown (role=menuitemradio evita falsos positivos)
            try:
                page.wait_for_selector('[role="menuitemradio"]', timeout=5000)
                for item in page.locator('[role="menuitemradio"]').all():
                    if item.inner_text().strip() == 'Precio':
                        item.click(timeout=3000)
                        break
                page.wait_for_timeout(4000)
            except:
                pass

            # Esperar resultados ordenados
            try:
                page.wait_for_selector('div[role="listitem"], div[role="list"]', timeout=25000)
            except:
                pass
            page.wait_for_timeout(3000)

            page.screenshot(path=f"result_{origin}_{destination}.png")

            price = extract_first_price_from_section(page)
            if price:
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
# 📲 TELEGRAM
# ============================================================

def send_telegram(message):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       message,
                "parse_mode": "Markdown",  # soporta *negrita* y _cursiva_
            },
            timeout=10,
        )
        if r.status_code != 200:
            print(f"    ⚠️  Telegram HTTP {r.status_code}: {r.text}")
        return r.status_code == 200
    except Exception as e:
        print(f"    ⚠️  Telegram error: {e}")
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
    if not os.path.exists(PRICES_FILE):
        save_prices({})

    if date.today() > STOP_DATE:
        print("✅ Monitoreo terminado (pasó el 30 de mayo).")
        return

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Revisando precios...")
    records = load_prices()
    summary_lines = [f"✈️ *Monitor de vuelos*\n_{datetime.now().strftime('%Y-%m-%d %H:%M')}_\n"]

    for route in ROUTES:
        key = f"{route['origin']}-{route['destination']}-{route['date']}"
        print(f"  🔍 {route['label']}...", end=" ", flush=True)

        price, details = get_cheapest_price(
            route["origin"], route["destination"], route["date"]
        )

        if price is None:
            print("sin resultados.")
            summary_lines.append(f"❓ {route['label']} ({route['date']}) — sin resultados\n")
            continue

        prev      = records.get(key, {}).get("price")
        fmt_price = f"$ {price:,.0f} COP"
        fmt_prev  = f"$ {prev:,.0f} COP" if prev else "ninguno aún"
        print(f"{fmt_price}  (mínimo anterior: {fmt_prev})")

        is_new_min = prev is None or price < prev
        if is_new_min:
            records[key] = {
                "price":    price,
                "details":  details,
                "found_at": str(datetime.now()),
            }
            save_prices(records)
            print(f"     → 🆕 NUEVO MÍNIMO guardado")

        tag = "🚨 *NUEVO MÍNIMO*" if is_new_min else "📊 Actual"
        safe_url = details['url'].replace("_", "%5F").replace("*", "%2A")
        summary_lines.append(
            f"{tag}\n"
            f"✈️ {route['label']}\n"
            f"📅 {route['date']}\n"
            f"💰 {fmt_price}\n"
            f"📉 mínimo guardado: {fmt_prev}\n"
            f"🔗 {safe_url}\n"
        )

    ok = send_telegram("\n".join(summary_lines))
    print(f"  Telegram: {'✅ enviado' if ok else '⚠️ falló'}")

check_prices()
