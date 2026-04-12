"""
✈️ COLOMBIA FLIGHT PRICE MONITOR
Google Flights scraping con Playwright
Diseñado para correr una vez por ejecución (GitHub Actions lo dispara cada 30 min)
"""

import re
import json
import requests
import os
import sys
from datetime import datetime, date

# Fix Windows console encoding (emojis)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

# ============================================================
# 🔧 CONFIGURACIÓN
# ============================================================

WHATSAPP_NUMBER  = "+573014091603"
CALLMEBOT_APIKEY = "9481713"

ROUTES = [
    {"origin": "BOG", "destination": "SMR", "date": "2026-06-07", "label": "Bogotá → Santa Marta"},
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

def extract_cop_prices(page):
    """Extrae precios en COP del texto visible."""
    cops = []
    try:
        body = page.inner_text("body")
        # Formato colombiano con puntos: 189.000 / 1.234.567
        for m in re.findall(r'\b(\d{1,3}(?:\.\d{3})+)\b', body):
            val = float(m.replace(".", ""))
            if 80_000 < val < 5_000_000:
                cops.append(val)
        # Formato con comas (US-style): 189,000 / 1,234,567
        if not cops:
            for m in re.findall(r'\b(\d{1,3}(?:,\d{3})+)\b', body):
                val = float(m.replace(",", ""))
                if 80_000 < val < 5_000_000:
                    cops.append(val)
    except:
        pass
    return cops

def select_airport(page, field_locator, code):
    """Escribe el código de aeropuerto y selecciona la opción que contiene ese código."""
    field_locator.click(timeout=4000)
    page.wait_for_timeout(400)
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.wait_for_timeout(200)
    page.keyboard.type(code, delay=80)
    page.wait_for_timeout(2500)
    # Seleccionar la opción que contiene el código IATA (evita clickear opciones de otro dropdown)
    page.wait_for_selector(f'[role="option"]:has-text("{code}")', timeout=5000)
    page.locator(f'[role="option"]:has-text("{code}")').first.click(timeout=3000)
    page.wait_for_timeout(800)


def get_cheapest_price(origin, destination, dep_date):
    usd_to_cop = get_usd_to_cop()

    MONTHS_ES = {
        "01": "enero",  "02": "febrero", "03": "marzo",    "04": "abril",
        "05": "mayo",   "06": "junio",   "07": "julio",    "08": "agosto",
        "09": "septiembre", "10": "octubre", "11": "noviembre", "12": "diciembre",
    }

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
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        price, details = None, None

        try:
            page.goto(
                "https://www.google.com/travel/flights?hl=es-419&curr=COP",
                wait_until="domcontentloaded", timeout=40000
            )
            # Esperar a que el formulario esté listo
            page.wait_for_selector('input[role="combobox"]', timeout=15000)
            page.wait_for_timeout(2000)
            accept_cookies(page)
            page.wait_for_timeout(1500)

            # 1. Cambiar a "Solo ida"
            # El trip type es SPAN[jsname="Fb0Bif"]. Usar Playwright .click() nativo
            # (incluye mousedown/mouseup/hover) en vez de dispatchEvent que no abre el dropdown.
            try:
                page.locator('span[jsname="Fb0Bif"]').first.click(timeout=4000)
                page.wait_for_timeout(1000)
                # Seleccionar "Solo ida" buscando cualquier elemento visible con ese texto
                solo_clicked = page.evaluate("""
                    (() => {
                        for (const el of document.querySelectorAll('*')) {
                            if (el.childElementCount === 0 && el.innerText && el.innerText.trim() === 'Solo ida') {
                                const r = el.getBoundingClientRect();
                                if (r.width > 0 && r.height > 0) {
                                    el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
                                    return true;
                                }
                            }
                        }
                        return false;
                    })()
                """)
                page.wait_for_timeout(700)
                if solo_clicked:
                    print("    ✅ Solo ida seleccionado")
                else:
                    print("    ⚠️  Solo ida no encontrado en dropdown")
            except Exception as e:
                print(f"    ⚠️  Trip type: {e}")

            page.screenshot(path=f"step1_{origin}_{destination}.png")

            # 2. Origen — jsname="yrriRe" nth(0)
            try:
                select_airport(page, page.locator('input[jsname="yrriRe"]').nth(0), origin)
                print(f"    ✅ Origen {origin} listo")
            except Exception as e:
                print(f"    ⚠️  Origen: {e}")

            # 3. Destino — después de seleccionar origen, Tab avanza al campo de destino.
            # Esto evita el problema de clickear un input con display:none.
            try:
                page.wait_for_timeout(400)
                page.keyboard.press("Tab")
                page.wait_for_timeout(600)
                page.keyboard.type(destination, delay=80)
                page.wait_for_timeout(2500)
                page.wait_for_selector(f'[role="option"]:has-text("{destination}")', timeout=5000)
                page.locator(f'[role="option"]:has-text("{destination}")').first.click(timeout=3000)
                page.wait_for_timeout(800)
                print(f"    ✅ Destino {destination} listo")
            except Exception as e:
                print(f"    ⚠️  Destino: {e}")

            page.screenshot(path=f"step3_{origin}_{destination}.png")

            # 4. Fecha de salida
            try:
                year, month, _ = dep_date.split("-")
                target_month = MONTHS_ES[month]

                # Abrir el date picker
                date_opened = False
                for date_sel in [
                    '[jsname="lBq2Xb"]',
                    '[aria-label*="Fecha de salida"]',
                    '[aria-label*="Salida"]',
                    'input[jsname="yrriRe"]:nth-of-type(3)',
                ]:
                    try:
                        page.click(date_sel, timeout=2000)
                        page.wait_for_timeout(1500)
                        date_opened = True
                        print(f"    ✅ Fecha picker abierto ({date_sel})")
                        break
                    except:
                        continue

                if not date_opened:
                    print("    ⚠️  No se pudo abrir el date picker")
                    raise Exception("date picker no abierto")

                # Navegar al mes correcto
                for _ in range(18):
                    try:
                        header = page.locator("h2").first.inner_text(timeout=1000).lower()
                        if target_month in header and year in header:
                            break
                    except:
                        pass
                    try:
                        page.click(
                            'button[aria-label*="siguiente"], '
                            'button[aria-label*="Next"], '
                            'button[aria-label*="Mes siguiente"]',
                            timeout=2000
                        )
                        page.wait_for_timeout(400)
                    except:
                        break

                page.click(f'[data-iso="{dep_date}"]', timeout=5000)
                page.wait_for_timeout(500)

                for done in ["Listo", "Done"]:
                    try:
                        page.click(f'button:has-text("{done}")', timeout=2000)
                        break
                    except:
                        continue
                print(f"    ✅ Fecha {dep_date} lista")
            except Exception as e:
                print(f"    ⚠️  Fecha: {e}")

            page.screenshot(path=f"step4_{origin}_{destination}.png")
            page.wait_for_timeout(500)

            # 5. Buscar — NO "Explorar" (ese abre explorar destinos, no buscar vuelo)
            searched = False
            for btn in ["Buscar", "Search"]:
                try:
                    page.click(f'button:has-text("{btn}")', timeout=3000)
                    searched = True
                    print(f"    ✅ Búsqueda iniciada ({btn})")
                    break
                except:
                    continue
            if not searched:
                page.keyboard.press("Enter")
                print("    ✅ Búsqueda iniciada (Enter)")

            # 6. Esperar resultados
            page.wait_for_timeout(5000)
            try:
                page.wait_for_selector('li[data-resultid], [role="listitem"]', timeout=15000)
                print("    ✅ Resultados cargados")
            except:
                print("    ⏳ Timeout esperando resultados, extrayendo lo que hay...")
                page.wait_for_timeout(5000)

            # 7. Screenshot
            page.screenshot(path=f"result_{origin}_{destination}.png")

            # 8. Extraer precios COP
            cops = extract_cop_prices(page)

            # Fallback USD→COP
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
