"""
Monitor de vuelos BOG → SMR · Solo ida · 7 de junio de 2026
Fuente: google.com.co/travel/flights (precios COP Colombia)
Guarda precio_minimo_actual y precio_minimo_historico en precios_minimos.json
"""

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime
from playwright.sync_api import sync_playwright

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Configuración ────────────────────────────────────────────────────────────
IATA_ORIGEN = "BOG"
IATA_DEST   = "SMR"
FECHA_VUELO = "2026-06-07"

# URL directa con fecha ya codificada en el parámetro tfs (estable para esta ruta/fecha)
URL_BUSQUEDA = (
    "https://www.google.com.co/travel/flights/search"
    "?tfs=CBwQAhoqEgoyMDI2LTA2LTA3ag0IAhIJL20vMDFkenljcg0IAxIJL20vMDJuc2xjQAFIAXABggELCP___________wGYAQI"
)

PHONE     = os.environ.get("CALLMEBOT_PHONE", "")
API_KEY   = os.environ.get("CALLMEBOT_APIKEY", "")
JSON_PATH = os.environ.get("JSON_PATH", "precios_minimos.json")
DEBUG     = os.environ.get("DEBUG", "1") == "1"
# ─────────────────────────────────────────────────────────────────────────────


def scrape_vuelos() -> dict:
    """Abre Google Flights directamente en la página de resultados y extrae vuelos."""
    precio_minimo = None
    vuelos = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-419",
            timezone_id="America/Bogota",
            viewport={"width": 1280, "height": 900},
            extra_http_headers={"Accept-Language": "es-CO,es;q=0.9"},
        )
        page = ctx.new_page()

        print(f"  → Abriendo resultados: BOG → SMR · {FECHA_VUELO}")
        page.goto(URL_BUSQUEDA, wait_until="domcontentloaded", timeout=60000)

        # Esperar que aparezcan las tarjetas de vuelo
        try:
            page.wait_for_selector("li.pIav2d, ul[role='list'] > li", timeout=20000)
            print("  → Tarjetas de vuelo detectadas.")
        except Exception:
            print("  ⚠️  Timeout en selector, continuando.")

        page.evaluate("() => new Promise(r => setTimeout(r, 2000))")

        # ── Activar pestaña "Más económicos" ────────────────────────────────
        try:
            resultado = page.evaluate("""
                () => {
                    const tabs = [...document.querySelectorAll('button, [role="tab"], div[role="button"]')];
                    const tab = tabs.find(t => t.innerText && t.innerText.includes('económicos'));
                    if (tab) { tab.click(); return 'clicked'; }
                    return 'not found';
                }
            """)
            print(f"  → Pestaña 'Más económicos': {resultado}")
            if resultado == "clicked":
                page.evaluate("() => new Promise(r => setTimeout(r, 2500))")
        except Exception as e:
            print(f"  ⚠️  Tab económicos: {e}")

        if DEBUG:
            page.screenshot(path="debug_screenshot.png", full_page=False)

        # ── Extraer vuelos individuales ──────────────────────────────────────
        items = page.query_selector_all("li.pIav2d, ul[role='list'] > li")
        print(f"  → {len(items)} ítems encontrados.")

        for item in items:
            texto = item.inner_text().strip()
            if "COP" in texto and len(texto) > 20:
                vuelos.append(texto)

        # ── Extraer todos los precios COP de la página ───────────────────────
        todos_precios = []
        texto_pagina = page.inner_text("body")
        for m in re.finditer(r"COP\s?([\d,\.]+)", texto_pagina):
            raw = re.sub(r"[,\.]", "", m.group(1))
            if raw and 40_000 < int(raw) < 5_000_000:
                todos_precios.append(int(raw))

        if todos_precios:
            todos_precios = sorted(set(todos_precios))
            print(f"  → Precios en lista: {todos_precios[:8]}")
            precio_minimo = todos_precios[0]

        # ── Entrar al primer vuelo directo para ver opciones de reserva ───────
        opcion_barata = {}
        try:
            vuelo_click = page.evaluate("""
                () => {
                    const items = document.querySelectorAll('li.pIav2d');
                    for (const item of items) {
                        if (item.innerText.includes('Directo')) {
                            const btn = [...item.querySelectorAll('button')]
                                .find(b => b.getAttribute('aria-label') === 'Seleccionar vuelo');
                            if (btn) {
                                // Capturar horario antes de hacer click
                                const horas = item.innerText.match(/\d{1,2}:\d{2}\s?[ap]\.m\./g) || [];
                                btn.click();
                                return { status: 'clicked', salida: horas[0] || '', llegada: horas[1] || '' };
                            }
                        }
                    }
                    return { status: 'not found' };
                }
            """)
            print(f"  → Entrar a opciones de reserva: {vuelo_click}")
            if vuelo_click.get("status") == "clicked":
                try:
                    page.wait_for_selector("text=Opciones de reserva", timeout=15000)
                except Exception:
                    page.evaluate("() => new Promise(r => setTimeout(r, 4000))")

                # Extraer pares (agencia, precio) del booking page
                opciones = page.evaluate("""
                    () => {
                        const lines = document.body.innerText.split('\\n').map(l => l.trim()).filter(l => l);
                        const res = [];
                        for (let i = 0; i < lines.length; i++) {
                            if (lines[i].startsWith('Reservar con')) {
                                const agencia = lines[i].replace('Reservar con', '').split('Aerolínea')[0].trim();
                                for (let j = i + 1; j < Math.min(i + 5, lines.length); j++) {
                                    const m = lines[j].match(/COP\\s?([\\d,\\.]+)/);
                                    if (m) {
                                        const precio = parseInt(m[1].replace(/[,.]/g, ''));
                                        if (precio > 40000 && precio < 500000) {
                                            res.push({ agencia, precio });
                                            break;
                                        }
                                    }
                                }
                            }
                        }
                        return res;
                    }
                """)
                print(f"  → Opciones reserva: {opciones}")
                if opciones:
                    mejor = min(opciones, key=lambda x: x["precio"])
                    opcion_barata = {
                        "agencia": mejor["agencia"],
                        "precio_cop": mejor["precio"],
                        "salida": vuelo_click.get("salida", ""),
                        "llegada": vuelo_click.get("llegada", ""),
                    }
                    precio_minimo = min(precio_minimo or mejor["precio"], mejor["precio"])
                    print(f"  → Mejor opción: {opcion_barata}")
        except Exception as e:
            print(f"  ⚠️  Opciones de reserva: {e}")

        browser.close()

    # Parsear vuelos individuales
    vuelos_parseados = []
    for v in vuelos[:8]:
        lineas = [l.strip() for l in v.splitlines() if l.strip()]
        precio_m = re.search(r"COP\s?([\d,\.]+)", v)
        precio_v = int(re.sub(r"[,\.]", "", precio_m.group(1))) if precio_m else None
        horas = re.findall(r"\d{1,2}:\d{2}\s?[ap]\.m\.", v)
        duracion = re.search(r"(\d\s?h\s?\d{0,2}\s?min)", v)
        escalas_txt = re.search(r"(Directo|\d\s?escala)", v, re.I)
        aerolinea = lineas[1] if len(lineas) > 1 else "Desconocida"

        if precio_v:
            vuelos_parseados.append({
                "aerolinea": aerolinea,
                "salida": horas[0] if horas else None,
                "llegada": horas[1] if len(horas) > 1 else None,
                "duracion": duracion.group(1) if duracion else None,
                "escalas": 0 if escalas_txt and "directo" in escalas_txt.group(1).lower() else 1,
                "precio_cop": precio_v,
            })

    vuelos_parseados.sort(key=lambda x: x["precio_cop"])
    return {"precio_minimo": precio_minimo, "vuelos": vuelos_parseados, "opcion_barata": opcion_barata}


def cargar_historial() -> dict:
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "ruta": f"{IATA_ORIGEN} → {IATA_DEST}",
        "origen": f"Bogotá ({IATA_ORIGEN})",
        "destino": f"Santa Marta ({IATA_DEST})",
        "fecha_vuelo": FECHA_VUELO,
        "precio_minimo_historico": None,
        "historial": [],
    }


def guardar_historial(data: dict) -> None:
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ JSON guardado: {JSON_PATH}")


def enviar_whatsapp(mensaje: str) -> None:
    if not PHONE or not API_KEY:
        print("⚠️  CALLMEBOT no configurado. Notificación omitida.")
        return
    params = urllib.parse.urlencode({"phone": PHONE, "text": mensaje, "apikey": API_KEY})
    url = f"https://api.callmebot.com/whatsapp.php?{params}"
    print(f"📲 URL: {url[:80]}...")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(f"📲 CallMeBot status: {resp.status}")
            print(f"📲 CallMeBot respuesta: {body}")
    except Exception as e:
        print(f"❌ Error WhatsApp: {e}")


def construir_mensaje(
    precio_actual: int,
    precio_anterior: int | None,
    precio_min_historico: int | None,
    opcion_barata: dict,
) -> str:
    fecha = datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")

    if precio_anterior is None:
        variacion = "Nueva consulta"
    elif precio_actual < precio_anterior:
        variacion = f"Bajo COP {precio_anterior - precio_actual:,}"
    elif precio_actual > precio_anterior:
        variacion = f"Subio COP {precio_actual - precio_anterior:,}"
    else:
        variacion = "Sin cambio"

    lineas = ["Monitor de vuelos BOG-SMR 7 jun 2026"]

    salida  = opcion_barata.get("salida", "")
    llegada = opcion_barata.get("llegada", "")
    agencia = opcion_barata.get("agencia", "")
    if salida and llegada:
        lineas.append(f"Vuelo: {salida} - {llegada} (directo)")
    lineas.append(f"Precio minimo: COP {precio_actual:,}")
    if agencia:
        lineas.append(f"Reservar en: {agencia}")
    lineas.append(f"Variacion: {variacion}")
    if precio_min_historico:
        lineas.append(f"Minimo historico: COP {precio_min_historico:,}")
        if precio_actual <= precio_min_historico:
            lineas.append("Nuevo minimo historico!")
    lineas.append(f"Consultado: {fecha}")
    return "\n".join(lineas)


def main():
    print(f"🔍 BOG → SMR · {FECHA_VUELO} · Solo ida")
    resultado = scrape_vuelos()
    precio_actual = resultado["precio_minimo"]

    if not precio_actual:
        print("❌ No se pudo obtener el precio.")
        if DEBUG:
            print("   Revisa debug_screenshot.png")
        raise SystemExit(1)

    print(f"💰 Precio mínimo: ${precio_actual:,} COP")

    historial = cargar_historial()
    precio_anterior = historial["historial"][-1]["precio_minimo_cop"] if historial["historial"] else None
    precio_min_historico = historial.get("precio_minimo_historico")

    es_nuevo_minimo = precio_min_historico is None or precio_actual < precio_min_historico

    # Actualizar mínimo histórico
    if es_nuevo_minimo:
        historial["precio_minimo_historico"] = precio_actual
        precio_min_historico = precio_actual
        print(f"🏆 Nuevo mínimo histórico: COP {precio_actual:,}")
    else:
        print(f"ℹ️  Sin nuevo mínimo (histórico: COP {precio_min_historico:,})")

    # Agregar entrada al historial (siempre)
    opcion_barata = resultado.get("opcion_barata", {})
    vuelo_barato = resultado["vuelos"][0] if resultado["vuelos"] else {}
    historial["historial"].append({
        "fecha_consulta": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "precio_minimo_cop": precio_actual,
        "aerolinea": vuelo_barato.get("aerolinea", ""),
        "tipo": "Directo" if vuelo_barato.get("escalas", 1) == 0 else "Con escala",
        "duracion_minima": vuelo_barato.get("duracion", ""),
        "vuelo_mas_barato": vuelo_barato,
        "opcion_reserva": opcion_barata,
    })

    guardar_historial(historial)

    # Notificar solo si hay nuevo mínimo histórico
    if es_nuevo_minimo:
        mensaje = construir_mensaje(precio_actual, precio_anterior, precio_min_historico, opcion_barata)
        print(f"\n📩 Mensaje:\n{mensaje}\n")
        enviar_whatsapp(mensaje)
    else:
        print("🔕 Sin notificación (precio no es nuevo mínimo)")


if __name__ == "__main__":
    main()
