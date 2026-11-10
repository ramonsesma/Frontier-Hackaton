"""Resume los registros del agente y senala lo que merece revision humana.

Uso:
    python tools/analyze_logs.py render.log            # archivo con las lineas "[agent ...]"
    cat render.log | python tools/analyze_logs.py -    # o por la entrada estandar
    python tools/analyze_logs.py render.log --json     # salida maquina en vez de Markdown

NO modifica el prompt ni el codigo: agrupa fallos y propone donde mirar. Cambiar una regla del
prompt sin revisarla cuesta caro sobre un conjunto de tareas que no se ha visto.
"""
import json
import re
import statistics
import sys
from collections import Counter

LINE = re.compile(r"\[agent (?P<tag>[\w-]+) \+(?P<t>\d+)s\] (?P<msg>.*)")
THOUSANDS = re.compile(r"respuesta:\s*\d{1,3}(?:[.,]\d{3}){2,}")
SECRET_WORDS = re.compile(r"contrase|password|token|api[ _-]?key|clave|secret", re.I)
CODE_LIKE = re.compile(r"\b(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{6,}\b")  # algo como ZZZ-1234-AAAA


def parse(lines):
    tasks: dict = {}
    for raw in lines:
        m = LINE.search(raw)
        if not m:
            continue
        t = tasks.setdefault(m["tag"], {"tag": m["tag"], "acciones": Counter(), "clics": Counter(),
                                        "fallos_accion": 0, "trace": None})
        msg = m["msg"]
        if msg.startswith("inicio tarea="):
            t["tarea"] = msg[len("inicio tarea="):].split(" escritorio=")[0].strip("'\" ")
        elif msg.startswith("fin estado="):
            est, _, resumen = msg[len("fin estado="):].partition(" resumen=")
            t["estado"], t["resumen"], t["secs"] = est.strip(), resumen, int(m["t"])
        elif msg.startswith("TRACE "):
            try:
                t["trace"] = json.loads(msg[len("TRACE "):])
            except ValueError:
                pass
        elif msg.startswith("accion "):
            name, _, arg = msg[len("accion "):].partition(" ")
            t["acciones"][name] += 1
            if name == "click":
                t["clics"][arg] += 1
        elif "la accion" in msg and "fallo" in msg:
            t["fallos_accion"] += 1
        elif msg.startswith("corte:"):
            t["corte"] = True
    return [t for t in tasks.values() if "estado" in t]


def cause(t) -> str:
    r = t.get("resumen", "")
    if t["estado"] == "terminada":
        return "terminada"
    if r.startswith("Se agoto el tiempo") or t.get("corte") or (t.get("trace") or {}).get("tiempo_agotado"):
        return "fallida: tiempo agotado"
    if r.startswith("No hubo tiempo para empezar"):
        return "fallida: sin tiempo al empezar"
    if "servicio del modelo fallo" in r:
        return "fallida: errores del modelo"
    if r.startswith("Se perdio la conexion"):
        return "fallida: conexion perdida"
    if r.startswith("Error interno"):
        return "fallida: error interno"
    if r.startswith("Tarea cancelada"):
        return "fallida: cancelada por otra tarea"
    return "fallida: decision del agente"


def analyze(tasks):
    causes = Counter(cause(t) for t in tasks)
    secs = [t["secs"] for t in tasks]
    flags = []
    for t in tasks:
        r = t.get("resumen", "")
        who = f"{t['tag']} ({t.get('tarea', '?')[:60]})"
        if THOUSANDS.search(r):
            flags.append((who, "respuesta numerica con separadores de miles"))
        if t["estado"] == "terminada" and SECRET_WORDS.search(r) and CODE_LIKE.search(r):
            flags.append((who, "el resumen parece incluir una clave o contrasena: revisar"))
        if t["estado"] == "terminada" and t["secs"] >= 200:
            flags.append((who, f"termino muy cerca del limite ({t['secs']} s)"))
        if t["clics"] and max(t["clics"].values()) >= 4:
            flags.append((who, "mismo clic repetido 4 o mas veces (posible bucle)"))
        if t["fallos_accion"]:
            flags.append((who, f"{t['fallos_accion']} accion(es) fallaron al ejecutarse"))
        if t["estado"] == "fallida" and re.search(r"no (existe|hay|se encuentra).{0,40}(boton|funci|opci)", r, re.I):
            flags.append((who, "fallida por 'no existe el boton/funcion': comprobar si de verdad no existe"))
    out = {"tareas": len(tasks), "causas": dict(causes),
           "segundos": {"mediana": statistics.median(secs) if secs else None,
                        "p90": sorted(secs)[int(0.9 * (len(secs) - 1))] if secs else None,
                        "max": max(secs) if secs else None},
           "para_revisar": flags}
    return out


HINTS = {
    "fallida: tiempo agotado": "Tiempo agotado: mirar cuantos pasos gasta cada tarea larga; probar un modelo mas rapido o "
                               "pistas de la app (NOTES_*); no subir el limite sin medir.",
    "fallida: errores del modelo": "Errores del modelo: revisar saldo, limites de tasa y el id del modelo.",
    "fallida: error interno": "Error interno: leer el 'error interno' del registro y anadir una prueba.",
    "fallida: conexion perdida": "Conexion perdida: ¿el escritorio se reinicio a mitad de tarea?",
}


def markdown(res) -> str:
    L = [f"# Resumen de {res['tareas']} tareas", "", "| Resultado | Tareas |", "| --- | --- |"]
    L += [f"| {k} | {v} |" for k, v in sorted(res["causas"].items(), key=lambda kv: -kv[1])]
    s = res["segundos"]
    L += ["", f"Tiempo por tarea: mediana {s['mediana']} s, p90 {s['p90']} s, maximo {s['max']} s", ""]
    if res["para_revisar"]:
        L += ["## Para revisar a mano", ""] + [f"- {w}: {m}" for w, m in res["para_revisar"]] + [""]
    hints = [h for k, h in HINTS.items() if k in res["causas"]]
    if hints:
        L += ["## Donde mirar", ""] + [f"- {h}" for h in hints] + [""]
    L.append("Nota: los 'estados' son los que reporto el agente; no sustituyen la nota del evaluador.")
    return "\n".join(L)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    src = sys.stdin if args[0] == "-" else open(args[0], encoding="utf-8", errors="replace")
    with src:
        tasks = parse(src)
    res = analyze(tasks)
    print(json.dumps(res, ensure_ascii=False, indent=2) if "--json" in argv else markdown(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
