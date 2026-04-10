"""
Monitor de vuelos BOG → SMR para el 7 de junio de 2026.
Compara el precio mínimo actual con el histórico y notifica por CallMeBot (WhatsApp).
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime
from playwright.sync_api import sync_playwright

# ── Configuración ────────────────────────────────────────────────────────────
ORIGEN       = "BOG"
DESTINO      = "SMR"
FECHA_VUELO  = "2026-06-07"          # YYYY-MM-DD
URL_SKYSCANNER = (
    "https://www.skyscanner.com.co/transport/flights/bog/smr/260607/"
    "?adultsv2=1&cabinclass=economy&childrenv2=&rtn=0"
    "&preferdirects=false&outboundaltsenabled=false&inboundaltsenabled=false"
    "&sortby=cheapest"
)

PHONE        = os.environ.get("CALLMEBOT_PHONE", "")   # ej: 573001234567
API_KEY      = os.environ.get("CALLMEBOT_APIKEY", "")  # token que te pasaron
JSON_PATH    = os.environ.get("JSON_PATH", "precios_minimos.json")

WAIT_RESULTS_MS = 12000   # ms a esperar que carguen los resultados
# ─────────────────────────────────────────────────────────────────────────────


def scrape_precio_minimo() -> dict:
    """Abre Skyscanner con Playwright y extrae los 5 vuelos más baratos."""
    vuelos = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            )
        )
        page.goto(URL_SKYSCANNER, timeout=60000)
        page.wait_for_timeout(WAIT_RESULTS_MS)

        # Intentar cerrar diálogos / modales si aparecen
        for selector in ["button[aria-label='Close']", "button[data-testid='dialog-close']"]:
            try:
                page.click(selector, timeout=2000)
            except Exception:
                pass

        # Extraer tarjetas de vuelo
        cards = page.query_selector_all("[data-testid='itinerary-container'], .FlightsResults_dayViewItems__x_BHy > div")

        if not cards:
            # fallback: buscar por aria-label con precio
            cards = page.query_selector_all("div[aria-label*='COP'], div[aria-label*='$']")

        for card in cards[:10]:
            texto = card.inner_text()
            vuelos.append(texto.strip())

        # Extraer precio mínimo del encabezado de resumen
        precio_minimo = None
        try:
            el = page.query_selector("[data-testid='cheapest-tab-price'], .BpkText_bpk-text--lg__ZGJlN")
            if el:
                raw = el.inner_text().replace("$", "").replace(".", "").replace(",", "").strip()
                precio_minimo = int(raw)
        except Exception:
            pass

        # Si no se encontró por selector específico, parsear de las tarjetas
        if not precio_minimo and vuelos:
            import re
            precios = []
            for v in vuelos:
                matches = re.findall(r"\$\s*([\d\.]+)", v)
                for m in matches:
                    try:
                        precios.append(int(m.replace(".", "")))
                    except ValueError:
                        pass
            if precios:
                precio_minimo = min(precios)

        browser.close()

    return {"precio_minimo": precio_minimo, "vuelos_raw": vuelos[:5]}


def cargar_historial() -> dict:
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "ruta": f"{ORIGEN} → {DESTINO}",
        "origen": ORIGEN,
        "destino": DESTINO,
        "fecha_vuelo": FECHA_VUELO,
        "historial": []
    }


def guardar_historial(data: dict) -> None:
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ JSON actualizado: {JSON_PATH}")


def enviar_whatsapp(mensaje: str) -> None:
    if not PHONE or not API_KEY:
        print("⚠️  CALLMEBOT_PHONE o CALLMEBOT_APIKEY no configurados. Notificación omitida.")
        return
    texto_encoded = urllib.parse.quote(mensaje)
    url = f"https://api.callmebot.com/whatsapp.php?phone={PHONE}&text={texto_encoded}&apikey={API_KEY}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            print(f"📲 WhatsApp enviado: {resp.status}")
    except Exception as e:
        print(f"❌ Error enviando WhatsApp: {e}")


def construir_mensaje(precio_actual: int, precio_anterior: int | None, vuelos_raw: list) -> str:
    fecha_consulta = datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")
    ruta = f"BOG → SMR · 7 jun 2026"

    if precio_anterior is None:
        variacion = "🆕 Primera consulta"
    elif precio_actual < precio_anterior:
        diff = precio_anterior - precio_actual
        variacion = f"📉 Bajó ${diff:,} COP"
    elif precio_actual > precio_anterior:
        diff = precio_actual - precio_anterior
        variacion = f"📈 Subió ${diff:,} COP"
    else:
        variacion = "➡️ Sin cambio"

    lineas = [
        f"✈️ *Monitor de vuelos*",
        f"Ruta: {ruta}",
        f"Precio mínimo: ${precio_actual:,} COP",
        f"Variación: {variacion}",
        f"Consultado: {fecha_consulta}",
    ]
    return "\n".join(lineas)


def main():
    print(f"🔍 Consultando vuelos {ORIGEN} → {DESTINO} para {FECHA_VUELO}...")
    resultado = scrape_precio_minimo()
    precio_actual = resultado["precio_minimo"]

    if not precio_actual:
        print("❌ No se pudo obtener el precio. Abortando.")
        return

    print(f"💰 Precio mínimo encontrado: ${precio_actual:,} COP")

    historial = cargar_historial()
    precio_anterior = historial["historial"][-1]["precio_minimo_cop"] if historial["historial"] else None

    # Agregar entrada al historial
    historial["historial"].append({
        "fecha_consulta": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "precio_minimo_cop": precio_actual,
        "vuelos_muestra": resultado["vuelos_raw"]
    })
    guardar_historial(historial)

    # Notificar siempre (o solo si cambió — ajusta la condición según prefieras)
    mensaje = construir_mensaje(precio_actual, precio_anterior, resultado["vuelos_raw"])
    print(f"\n📩 Mensaje:\n{mensaje}\n")
    enviar_whatsapp(mensaje)


if __name__ == "__main__":
    main()
