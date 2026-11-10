"""Prueba del analizador de registros con lineas sinteticas (sin red)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import analyze_logs as A  # noqa: E402

LOG = """\
[agent aaa +0s] inicio tarea='Crea el cliente X' escritorio=wss://d1/websockify
[agent aaa +5s] accion click {"x": 10, "y": 20}
[agent aaa +60s] fin estado=terminada resumen=Cliente creado, codigo CL-1
[agent bbb +0s] inicio tarea='Cuanto debe Y' escritorio=wss://d2/websockify
[agent bbb +70s] fin estado=terminada resumen=Total. respuesta: 2.275.000
[agent ccc +0s] inicio tarea='Anula la factura' escritorio=wss://d3/websockify
[agent ccc +20s] accion click {"x": 5, "y": 5}
[agent ccc +25s] accion click {"x": 5, "y": 5}
[agent ccc +30s] accion click {"x": 5, "y": 5}
[agent ccc +35s] accion click {"x": 5, "y": 5}
[agent ccc +264s] corte: quedan 6s, pasos=40
[agent ccc +264s] fin estado=fallida resumen=Se agoto el tiempo antes de poder confirmar que la tarea quedo completa. Tarea: x
[agent ddd +0s] inicio tarea='Copia la clave' escritorio=wss://d4/websockify
[agent ddd +20s] fin estado=terminada resumen=La clave de integracion es ZZZ-1234-AAAA
[agent eee +0s] inicio tarea='Exporta el reporte' escritorio=wss://d5/websockify
[agent eee +30s] fin estado=fallida resumen=No hay boton de exportar en esta pantalla
[agent eee +30s] TRACE {"estado": "fallida", "secs": 30}
linea ajena sin formato
"""

tasks = A.parse(LOG.splitlines())
assert len(tasks) == 5
causas = {t["tag"]: A.cause(t) for t in tasks}
assert causas["aaa"] == "terminada" and causas["ccc"] == "fallida: tiempo agotado", causas
assert causas["eee"] == "fallida: decision del agente", causas
res = A.analyze(tasks)
assert res["tareas"] == 5 and res["causas"]["fallida: tiempo agotado"] == 1, res
avisos = " | ".join(f"{w} {m}" for w, m in res["para_revisar"])
for esperado in ["separadores de miles", "clave o contrasena", "mismo clic repetido", "'no existe el boton/funcion'"]:
    assert esperado in avisos, (esperado, avisos)
assert res["segundos"]["max"] == 264
md = A.markdown(res)
assert "Para revisar a mano" in md and "Tiempo agotado" in md and "no sustituyen" in md
assert A.parse([]) == [] and A.analyze([])["tareas"] == 0
print("ANALYZE OK")
