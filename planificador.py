"""
🧳 PLANIFICADOR DE VIAJES
Recorre rangos de fechas (definidos en trips.json) de a pocas consultas por corrida,
guarda los precios en trip_prices.json y arma la combinación ida+vuelta más barata
para viajes en grupo con personas en distintas ciudades.

Avisa por Telegram cuando:
  • un vuelo queda en su precio objetivo (y es más bajo que el último aviso),
  • aparece una combinación de grupo más barata que la mejor anterior,
  • una vez al día (desde las 8 a.m.) con el resumen.
"""

import os
import re
import sys
import json
from datetime import date, datetime, timedelta
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

from monitor_vuelos import get_cheapest_price, send_telegram

sys.stdout.reconfigure(encoding="utf-8")

TRIPS_FILE      = os.environ.get("TRIPS_FILE", "trips.json")
STATE_FILE      = os.environ.get("TRIP_PRICES_FILE", "trip_prices.json")
QUERIES_PER_RUN = int(os.environ.get("QUERIES_PER_RUN", "24"))
WORKERS         = int(os.environ.get("WORKERS", "2"))
SUMMARY_HOUR    = int(os.environ.get("SUMMARY_HOUR", "8"))

DIAS  = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

# ============================================================
# utilidades
# ============================================================

def daterange(a, b):
    d, end = date.fromisoformat(a), date.fromisoformat(b)
    while d <= end:
        yield d
        d += timedelta(days=1)

def fmt_cop(v):
    return "$" + f"{v:,.0f}".replace(",", ".")

def fmt_date(iso):
    d = date.fromisoformat(iso)
    return f"{DIAS[d.weekday()]} {d.day} {MESES[d.month - 1]}"

def gf_url(route, iso):
    o, d = route.split("-")
    q = quote(f"Flights from {o} to {d} on {iso} oneway")
    return f"https://www.google.com/travel/flights?q={q}&curr=COP&hl=es-419"

TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])", re.IGNORECASE)

def to_minutes(t):
    m = TIME_RE.search(t or "")
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    h = h % 12 + (12 if ap == "p" else 0)
    return h * 60 + mi

def clean_airline(a):
    """Google a veces antepone '+1' (llega al día siguiente)."""
    if not a:
        return None, False
    a = a.replace("\n", " ").strip()
    overnight = a.startswith("+1")
    return a.lstrip("+1").strip() or None, overnight

def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False, sort_keys=True)

# ============================================================
# 1. escaneo rotativo
# ============================================================

def wanted_keys(cfg):
    today = date.today()
    keys = []
    for s in cfg.get("scans", []):
        for d in daterange(s["from"], s["to"]):
            if d < today:
                continue
            for r in s["routes"]:
                k = f"{r}|{d}"
                if k not in keys:
                    keys.append(k)
    return keys

def pick_batch(keys, prices, n):
    """Primero las nunca consultadas, luego las más viejas."""
    def stale(k):
        p = prices.get(k)
        return p.get("last_attempt", "") if p else ""
    return sorted(keys, key=stale)[:n]

def scrape(key):
    route, iso = key.split("|")
    o, d = route.split("-")
    try:
        price, det = get_cheapest_price(o, d, iso)
    except Exception as e:
        print(f"  ⚠️ {key}: {e}")
        price, det = None, None
    return key, price, det or {}

def run_scan(cfg, state):
    prices = state["prices"]
    keys = wanted_keys(cfg)
    # limpiar fechas pasadas
    for k in list(prices):
        if k.split("|")[1] < str(date.today()):
            del prices[k]
    batch = pick_batch(keys, prices, QUERIES_PER_RUN)
    print(f"🔍 Consultando {len(batch)} de {len(keys)} rutas/fechas...")
    now = datetime.now().isoformat(timespec="minutes")
    with ThreadPoolExecutor(WORKERS) as ex:
        for key, price, det in ex.map(scrape, batch):
            rec = prices.setdefault(key, {})
            rec["last_attempt"] = now
            if price:
                airline, overnight = clean_airline(det.get("airline"))
                rec.update({
                    "price": price, "airline": airline, "overnight": overnight,
                    "dep": det.get("dep_time"), "arr": det.get("arr_time"),
                    "checked": now,
                })
                print(f"  ✓ {key}: {fmt_cop(price)} {airline or ''}")
            else:
                print(f"  ✗ {key}: sin resultados (se conserva el precio anterior)")
    return len(keys)

# ============================================================
# 2. combinaciones para viajes en grupo
# ============================================================

def leg(prices, route, iso):
    p = prices.get(f"{route}|{iso}")
    return p if p and p.get("price") else None

def best_way(prices, origin, dest, iso, hubs, min_conn):
    """Directo o con conexión por otra ciudad del grupo el mismo día."""
    options = []
    direct = leg(prices, f"{origin}-{dest}", iso)
    if direct:
        options.append((direct["price"], [(f"{origin}-{dest}", direct)]))
    for hub in hubs:
        if hub in (origin, dest):
            continue
        a, b = leg(prices, f"{origin}-{hub}", iso), leg(prices, f"{hub}-{dest}", iso)
        if not (a and b) or a.get("overnight"):
            continue
        arr, dep = to_minutes(a.get("arr")), to_minutes(b.get("dep"))
        if arr is None or dep is None or dep - arr < min_conn:
            continue
        options.append((a["price"] + b["price"], [(f"{origin}-{hub}", a), (f"{hub}-{dest}", b)]))
    return min(options, key=lambda x: x[0]) if options else None

def group_combos(gt, prices):
    dest, trav = gt["destination"], gt["travelers"]
    hubs, min_conn = list(trav), gt.get("min_connection_minutes", 120)
    today = date.today()
    days = [d for d in daterange(gt["from"], gt["to"]) if d >= today]
    combos = []
    for d1 in days:
        for n in range(gt["min_days"], gt["max_days"] + 1):
            d2 = d1 + timedelta(days=n)
            if d2 > date.fromisoformat(gt["to"]) + timedelta(days=gt["max_days"]):
                continue
            total, parts, ok = 0, [], True
            for city, count in trav.items():
                out = best_way(prices, city, dest, str(d1), hubs, min_conn)
                ret = best_way(prices, dest, city, str(d2), hubs, min_conn)
                if not (out and ret):
                    ok = False
                    break
                total += count * (out[0] + ret[0])
                parts.append((city, count, out, ret))
            if ok:
                combos.append({"total": total, "d1": str(d1), "d2": str(d2), "n": n, "parts": parts})
    combos.sort(key=lambda c: c["total"])
    return combos

def meets_target(combo, target):
    return all(l["price"] <= target
               for _, _, out, ret in combo["parts"]
               for way in (out, ret) for _, l in way[1])

def describe_way(way):
    legs = way[1]
    if len(legs) == 1:
        r, l = legs[0]
        return f"{fmt_cop(l['price'])} {l.get('airline') or ''} {l.get('dep') or ''}".strip()
    hub = legs[0][0].split("-")[1]
    txt = " + ".join(f"{r} {fmt_cop(l['price'])} ({l.get('dep') or '?'})" for r, l in legs)
    return f"{fmt_cop(way[0])} vía {hub}: {txt}"

def describe_combo(i, c, gt):
    people = sum(gt["travelers"].values())
    lines = [f"*{i}) {fmt_date(c['d1'])} → {fmt_date(c['d2'])}* ({c['n']} días): "
             f"*{fmt_cop(c['total'])}* total (~{fmt_cop(c['total'] / people)}/persona)"]
    for city, count, out, ret in c["parts"]:
        who = f"{city} ×{count}"
        lines.append(f"   • {who} ida: {describe_way(out)}")
        lines.append(f"   • {who} vuelta: {describe_way(ret)}")
    if meets_target(c, gt.get("target_per_flight", 0)):
        lines.append(f"   🎯 *Todos los vuelos ≤ {fmt_cop(gt['target_per_flight'])}*")
    return "\n".join(lines)

# ============================================================
# 3. avisos
# ============================================================

def flight_alerts(cfg, state):
    """Vuelos sueltos en precio objetivo; solo avisa si bajó respecto al último aviso."""
    prices, alerted = state["prices"], state["alerts"]
    rules = []  # (ruta, desde, hasta, límite, etiqueta)
    for sc in cfg.get("scans", []):
        if sc.get("alert_below"):
            for r in sc["routes"]:
                rules.append((r, sc["from"], sc["to"], sc["alert_below"], sc["label"]))
    for gt in cfg.get("group_trips", []):
        t, dest = gt.get("target_per_flight"), gt["destination"]
        if t:
            for city in gt["travelers"]:
                for r in (f"{city}-{dest}", f"{dest}-{city}"):
                    rules.append((r, gt["from"], gt["to"], t, gt["label"]))
    out = []
    for key, rec in sorted(prices.items()):
        route, iso = key.split("|")
        match = [(lim, lab) for r, a, b, lim, lab in rules if r == route and a <= iso <= b]
        if not match or not rec.get("price"):
            continue
        limit, label = match[0]
        if rec["price"] <= limit and rec["price"] < alerted.get(key, float("inf")):
            alerted[key] = rec["price"]
            url = gf_url(route, iso).replace("_", "%5F")
            out.append(f"🎯 {route} {fmt_date(iso)}: *{fmt_cop(rec['price'])}* "
                       f"{rec.get('airline') or ''} {rec.get('dep') or ''} ({label})\n   {url}")
    return out

def cheapest_lines(cfg, prices, top=3):
    lines = []
    for s in cfg.get("scans", []):
        if len(s["routes"]) != 1:
            continue
        r = s["routes"][0]
        recs = sorted(((k.split("|")[1], v) for k, v in prices.items()
                       if k.startswith(r + "|") and v.get("price")
                       and s["from"] <= k.split("|")[1] <= s["to"]),
                      key=lambda x: x[1]["price"])[:top]
        if recs:
            lines.append(f"✈️ *{s['label']}* (más baratos):")
            lines += [f"   • {fmt_date(d)}: {fmt_cop(v['price'])} {v.get('airline') or ''} "
                      f"{v.get('dep') or ''}→{v.get('arr') or ''}" for d, v in recs]
    return lines

# ============================================================
# main
# ============================================================

def main():
    cfg = load_json(TRIPS_FILE, {})
    state = load_json(STATE_FILE, {})
    for k in ("prices", "alerts", "best"):
        state.setdefault(k, {})

    total_keys = run_scan(cfg, state)
    prices = state["prices"]
    covered = sum(1 for k in wanted_keys(cfg) if prices.get(k, {}).get("price"))

    msgs = flight_alerts(cfg, state)

    summary = []
    for gt in cfg.get("group_trips", []):
        combos = group_combos(gt, prices)
        if not combos:
            continue
        best, prev = combos[0], state["best"].get(gt["label"])
        sig = f"{best['d1']}>{best['d2']}"
        if prev is None or best["total"] < prev["total"]:
            msgs.append(f"🏝️ *Nuevo mejor plan: {gt['label']}*"
                        + (f" (antes {fmt_cop(prev['total'])})" if prev else "")
                        + "\n" + describe_combo(1, best, gt))
        state["best"][gt["label"]] = {"total": best["total"], "plan": sig,
                                      "at": datetime.now().isoformat(timespec="minutes")}
        summary.append(f"🏝️ *{gt['label']}* — mejores opciones:")
        summary += [describe_combo(i + 1, c, gt) for i, c in enumerate(combos[:3])]

    now = datetime.now()
    send_summary = (now.hour >= SUMMARY_HOUR and state.get("last_summary") != str(now.date())) \
        or os.environ.get("FORCE_SUMMARY") == "1"

    parts = []
    if msgs:
        parts.append("🔔 *Planificador de viajes*\n\n" + "\n\n".join(msgs))
    if send_summary:
        body = cheapest_lines(cfg, prices) + [""] + summary
        body.append(f"\n_Cobertura: {covered}/{total_keys} rutas-fechas con precio_")
        parts.append("📋 *Resumen diario del planificador*\n\n" + "\n".join(body))
        state["last_summary"] = str(now.date())

    for p in parts:
        # Telegram corta en 4096 caracteres
        ok = send_telegram(p[:4000])
        print(f"Telegram: {'✅' if ok else '⚠️'}")
    if not parts:
        print("Sin novedades para avisar.")

    save_state(state)

if __name__ == "__main__":
    main()
