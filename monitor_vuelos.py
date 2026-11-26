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

ROUTES      = json.loads(os.environ.get("ROUTES_JSON", "[]"))
STOP_DATE   = date.fromisoformat(os.environ.get("STOP_DATE", "2099-12-31"))
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


PRICE_RE    = re.compile(r'COP\s*(\d{1,3}(?:[.,]\d{3})+)', re.IGNORECASE)
TIME_RE     = re.compile(r'\d{1,2}:\d{2}\s?[ap]\.?\s?m\.?', re.IGNORECASE)
DURATION_RE = re.compile(r'\d+\s*h(?:\s*\d+\s*min)?')

def extract_first_price_from_section(page):
    """Primer precio COP de 'Todos los vuelos' (ya ordenado por precio) + hora salida/llegada/aerolínea."""
    try:
        body = page.inner_text("body")
        idx = body.find("Todos los vuelos")
        section = body[idx:idx + 2000] if idx >= 0 else body
        m = PRICE_RE.search(section)
        if m:
            val = float(m.group(1).replace(".", "").replace(",", ""))
            if 20_000 < val < 5_000_000:
                pre = section[:m.start()]
                times = TIME_RE.findall(pre)
                dep_time = times[-2] if len(times) >= 2 else None
                arr_time = times[-1] if len(times) >= 1 else None

                airline = None
                if arr_time:
                    arr_pos = pre.rfind(arr_time)
                    after_arr = pre[arr_pos + len(arr_time):]
                    dur_m = DURATION_RE.search(after_arr)
                    raw = after_arr[:dur_m.start()] if dur_m else after_arr
                    raw = raw.strip(" \n\t–—-")
                    if raw:
                        airline = raw.split("Operado por")[0].strip()

                return val, dep_time, arr_time, airline
    except:
        pass
    return None, None, None, None

def _scrape_once(pw, origin, destination, dep_date, tag):
    """Carga Google Flights: Más económicos → Ordenado por precio → primer precio."""
    from urllib.parse import quote
    q = quote(f"Flights from {origin} to {destination} on {dep_date} oneway")
    url = f"https://www.google.com/travel/flights?q={q}&curr=COP&hl=es-419"

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

        # Google a veces devuelve una página de error transitoria ("Se produjo un error")
        if page.locator("text=Se produjo un error").count() > 0:
            print("    ⚠️  Google devolvió página de error, reintentando carga...")
            try:
                page.click("text=Volver a cargar", timeout=5000)
                page.wait_for_timeout(4000)
            except:
                pass

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

        page.screenshot(path=f"result_{origin}_{destination}_{dep_date}_{tag}.png")

        price, dep_time, arr_time, airline = extract_first_price_from_section(page)
        if price:
            details = {
                "source":    "Google Flights",
                "url":       page.url,
                "dep_time":  dep_time,
                "arr_time":  arr_time,
                "airline":   airline,
            }

    except Exception as e:
        print(f"    ⚠️  Error: {e}")
        try:
            page.screenshot(path=f"debug_{origin}_{destination}_{dep_date}_{tag}.png", full_page=True)
        except:
            pass

    browser.close()
    return price, details

def get_cheapest_price(origin, destination, dep_date, max_attempts=2):
    """Reintenta si Google no devuelve resultados (error transitorio anti-bot)."""
    with sync_playwright() as pw:
        for attempt in range(1, max_attempts + 1):
            price, details = _scrape_once(pw, origin, destination, dep_date, f"a{attempt}")
            if price:
                return price, details
            if attempt < max_attempts:
                print(f"    ↻ sin resultados, reintentando ({attempt}/{max_attempts})...")
    return None, None

# ============================================================
# 🟣 WINGO.COM (precio directo de la aerolínea)
# ============================================================

WINGO_ALERT_BELOW = float(os.environ.get("WINGO_ALERT_BELOW", "100000"))

_MESES_ES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
             "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
WINGO_STRIP_RE = re.compile(
    r"(?:Lun|Mar|Mié|Jue|Vie|Sáb|Dom)\s+(\d{1,2})\s+([A-Za-z]{3})\s+"
    r"(?:\$\s*([\d.,]+)\s*COP|Sin Vuelos)", re.IGNORECASE)

def wingo_url(origin, destination, iso):
    return f"https://booking.wingo.com/es/search/{origin}/{destination}/{iso}/1/0/0/1/COP/0/0"

def parse_wingo_strip(text, center_iso):
    """Tira de fechas de wingo.com → {iso: precio o None (sin vuelos)}."""
    c = date.fromisoformat(center_iso)
    out = {}
    for day, mon, price in WINGO_STRIP_RE.findall(text):
        m = _MESES_ES.get(mon.lower())
        if not m:
            continue
        y = c.year + (1 if m - c.month < -6 else -1 if m - c.month > 6 else 0)  # cruce de año
        iso = date(y, m, int(day)).isoformat()
        out[iso] = float(price.replace(",", "").replace(".", "")) if price else None
    return out

def wingo_prices(origin, destination, dates, attempts=2):
    """Precio más barato por día en wingo.com para las fechas pedidas.
    Cada carga muestra ~7 días alrededor de la fecha buscada."""
    wanted = sorted(set(dates))
    found = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            locale="es-CO", timezone_id="America/Bogota",
            viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        while True:
            missing = [d for d in wanted if d not in found]
            if not missing:
                break
            # centrar la búsqueda 3 días después de la primera fecha faltante
            center = min(date.fromisoformat(missing[0]).toordinal() + 3,
                         date.fromisoformat(wanted[-1]).toordinal())
            center = date.fromordinal(center).isoformat()
            got = {}
            for _ in range(attempts):
                try:
                    page.goto(wingo_url(origin, destination, center),
                              wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector("text=COP", timeout=30000)
                    page.wait_for_timeout(4000)
                    got = parse_wingo_strip(page.inner_text("body"), center)
                    if got:
                        break
                except Exception as e:
                    print(f"    ⚠️  Wingo {center}: {e}")
            if missing[0] not in got:
                # no se pudo leer esa fecha: marcarla para no quedar en bucle
                got[missing[0]] = "error"
            found.update(got)
        browser.close()
    return {d: found.get(d) for d in wanted}

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

NOTIFY_MODE = os.environ.get("NOTIFY_MODE", "always")   # "changes" = solo si hay novedades
WORKERS     = int(os.environ.get("WORKERS", "3"))

DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]

def check_prices():
    from concurrent.futures import ThreadPoolExecutor

    if not os.path.exists(PRICES_FILE):
        save_prices({})

    if date.today() > STOP_DATE:
        print("✅ Monitoreo terminado (pasó STOP_DATE).")
        return

    now = datetime.now()
    print(f"[{now.strftime('%Y-%m-%d %H:%M')}] Revisando precios...")
    records = load_prices()
    alerts  = records.setdefault("_alerts", {})

    routes = []
    for route in ROUTES:
        if date.fromisoformat(route["date"]) < date.today():
            print(f"  ⏭️  {route['label']} ({route['date']}) ya pasó, se omite.")
        else:
            routes.append(route)

    def fetch(route):
        try:
            return get_cheapest_price(route["origin"], route["destination"], route["date"])
        except Exception as e:
            print(f"  ⚠️ {route['date']}: {e}")
            return None, None

    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(fetch, routes))

    target_hits, blocks, compact = [], [], []

    for route, (price, details) in zip(routes, results):
        key = f"{route['origin']}-{route['destination']}-{route['date']}"
        d   = date.fromisoformat(route["date"])
        day = f"{DIAS[d.weekday()]} {d.day}/{d.month}"
        od  = f"{route['origin']}→{route['destination']}"

        if price is None:
            print(f"  🔍 {od} {route['date']}: sin resultados.")
            compact.append(f"❓ {day} {od}: sin resultados")
            continue

        prev    = records.get(key, {}).get("price")
        history = records.get(key, {}).get("history", [])
        fmt_price = f"$ {price:,.0f} COP"
        fmt_prev  = f"$ {prev:,.0f} COP" if prev else "ninguno aún"
        print(f"  🔍 {od} {route['date']}: {fmt_price}  (mínimo anterior: {fmt_prev})")

        is_new_min = prev is None or price < prev
        if is_new_min:
            history = (history + [{
                "price":    price,
                "airline":  details.get("airline"),
                "found_at": str(now),
            }])[-5:]
            records[key] = {
                "price":    price,
                "details":  details,
                "found_at": str(now),
                "history":  history,
            }
            print("     → 🆕 NUEVO MÍNIMO guardado")

        alert_below = route.get("alert_below")
        hit_target  = alert_below is not None and price <= alert_below
        new_target  = hit_target and price < alerts.get(key, float("inf"))
        if new_target:
            alerts[key] = price

        dep_time = details.get("dep_time") or ""
        arr_time = details.get("arr_time") or ""
        airline  = " ".join((details.get("airline") or "no disponible").split())
        mark = "🎯" if hit_target else ("🆕" if is_new_min else "•")
        compact.append(f"{mark} {day} {od}: $ {price:,.0f} {airline} {dep_time}")

        if not (new_target or is_new_min):
            continue
        if new_target:
            target_hits.append(f"{od} {day}: {fmt_price} ({airline} {dep_time})")
            tag = f"🎯 *PRECIO OBJETIVO* (≤ $ {alert_below:,.0f})"
        else:
            tag = "🚨 *NUEVO MÍNIMO*"
        safe_url = details["url"].replace("_", "%5F").replace("*", "%2A")
        blocks.append("\n".join([
            tag,
            f"✈️ {od} — 📅 {day} ({route['date']})",
            f"🕐 {dep_time} → {arr_time} · 🏷️ {airline}",
            f"💰 {fmt_price} (mínimo antes: {fmt_prev})",
            f"🔗 {safe_url}",
            "",
        ]))

    # ---- 🟣 Wingo directo: solo avisa si queda en WINGO_ALERT_BELOW o menos ----
    wingo_compact = []
    by_pair = {}
    for route in routes:
        by_pair.setdefault((route["origin"], route["destination"]), []).append(route["date"])
    for (o, dst), dates in by_pair.items():
        try:
            wp = wingo_prices(o, dst, dates)
        except Exception as e:
            print(f"  ⚠️ Wingo {o}-{dst}: {e}")
            continue
        parts = []
        for iso, wprice in wp.items():
            d = date.fromisoformat(iso)
            day = f"{DIAS[d.weekday()]} {d.day}/{d.month}"
            if not isinstance(wprice, float):
                parts.append(f"{d.day}/{d.month} {'sin vuelos' if wprice is None else '?'}")
                continue
            parts.append(f"{d.day}/{d.month} $ {wprice:,.0f}")
            print(f"  🟣 Wingo.com {o}→{dst} {iso}: $ {wprice:,.0f}")
            wkey = f"WINGO-{o}-{dst}-{iso}"
            if wprice <= WINGO_ALERT_BELOW and wprice < alerts.get(wkey, float("inf")):
                alerts[wkey] = wprice
                target_hits.append(f"🟣 Wingo.com {o}→{dst} {day}: $ {wprice:,.0f} COP")
                blocks.append("\n".join([
                    f"🟣 *WINGO.COM ≤ $ {WINGO_ALERT_BELOW:,.0f}*",
                    f"✈️ {o}→{dst} — 📅 {day} ({iso})",
                    f"💰 $ {wprice:,.0f} COP (precio directo en la aerolínea)",
                    f"🔗 {wingo_url(o, dst, iso).replace('_', '%5F')}",
                    "",
                ]))
        wingo_compact.append(f"🟣 Wingo.com {o}→{dst}: " + " · ".join(parts))

    save_prices(records)

    if NOTIFY_MODE == "changes" and not blocks:
        print("  Sin novedades, no se envía Telegram.")
        return

    lines = [f"✈️ *Monitor de vuelos*\n_{now.strftime('%Y-%m-%d %H:%M')}_\n"]
    if target_hits:
        lines.append("🎯🎯 *¡VUELO EN TU PRECIO OBJETIVO!* 🎯🎯\n" + "\n".join(target_hits) + "\n")
    lines += blocks
    lines.append("📋 *Todas las fechas (precio actual):*\n" + "\n".join(compact + wingo_compact))

    ok = send_telegram("\n".join(lines)[:4000])
    print(f"  Telegram: {'✅ enviado' if ok else '⚠️ falló'}")

if __name__ == "__main__":
    check_prices()
