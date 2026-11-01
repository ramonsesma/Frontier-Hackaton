"""Bucle real del agente con un modelo y un escritorio falsos (sin red)."""
import asyncio
import contextlib
import io
import json
import time
from types import SimpleNamespace as NS

import agent

PASS = "clave-escritorio-123"
CALLS = []   # lo que recibe el "modelo" en cada paso
ACTS = []    # lo que recibe el "escritorio"


class FakeRFB:
    def __init__(self, url, password):
        self.width, self.height = 1280, 800
        self.closed = False

    async def connect(self): pass
    async def screenshot(self, settle=1.0): return b"raw"
    def png(self, shot): return b"\x89PNG-fake"
    async def click(self, x, y, button=1, count=1): ACTS.append(("click", x, y))
    async def press(self, combo): ACTS.append(("key", combo))
    async def type_text(self, t): ACTS.append(("type", t))
    async def scroll(self, *a): ACTS.append(("scroll",) + a)
    async def close(self): self.closed = True


def tc(i, name, **args):
    return NS(id=f"call_{i}", function=NS(name=name, arguments=json.dumps(args)))


SCRIPT = [
    ("miro", [tc(1, "click", x=100, y=200)]),
    ("escribo", [tc(2, "type", text="Papelería", replace=True)]),
    ("termino", [tc(3, "finish", estado="terminada",
                    resumen=f"Cliente creado (clave {PASS})", respuesta="7")]),
]


class FakeClient:
    def __init__(self, *a, **k):
        self.chat = NS(completions=NS(create=self.create))
        self.n = 0

    async def create(self, **kw):
        CALLS.append(json.loads(json.dumps(kw["messages"])))
        content, calls = SCRIPT[self.n]
        self.n += 1
        return NS(choices=[NS(message=NS(content=content, tool_calls=calls))],
                  usage=NS(prompt_tokens=1000 * self.n, completion_tokens=50))


async def main():
    agent.RFBClient = FakeRFB
    agent.AsyncOpenAI = FakeClient
    out = {}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        await agent.run_task("Crea al cliente X", "wss://d/websockify", PASS, ["tok-secreto"],
                             lambda e, r: out.update(e=e, r=r), t_start=time.monotonic(), tag="abc12345")
    logs = buf.getvalue()

    # resultado: terminada, con respuesta y sin la contrasena
    assert out["e"] == "terminada", out
    assert "respuesta: 7" in out["r"] and PASS not in out["r"], out
    # acciones ejecutadas en el escritorio, en orden (type con replace -> ctrl+a antes)
    assert ACTS == [("click", 100, 200), ("key", "ctrl+a"), ("type", "Papelería")], ACTS
    # el registro cuenta los pasos y no filtra secretos
    for frag in ["inicio tarea=", "conectado 1280x800", "paso 1:", "accion click", "accion type",
                 "accion finish", "fin estado=terminada", "[agent abc12345"]:
        assert frag in logs, (frag, logs)
    assert PASS not in logs and "tok-secreto" not in logs, logs
    assert "PNG" not in logs and "base64" not in logs
    # mensajes validos para la API: cada tool_call tiene su respuesta 'tool' antes del siguiente user
    msgs = CALLS[-1]
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    ids_call = [t["id"] for m in msgs if m["role"] == "assistant" for t in m.get("tool_calls", [])]
    ids_tool = [m["tool_call_id"] for m in msgs if m["role"] == "tool"]
    assert ids_call == ids_tool == ["call_1", "call_2"], (ids_call, ids_tool)
    print(logs)
    print("AGENT OK")


def _make_client(script):
    """Cliente falso: cada elemento de script es una respuesta (content, calls) o una excepcion."""
    class C:
        def __init__(self, *a, **k):
            self.chat = NS(completions=NS(create=self.create))
            self.n = 0

        async def create(self, **kw):
            item = script[self.n]
            self.n += 1
            if isinstance(item, Exception):
                raise item
            if item == "EMPTY":
                return NS(choices=[], usage=None)
            content, calls = item
            return NS(choices=[NS(message=NS(content=content, tool_calls=calls))],
                      usage=NS(prompt_tokens=1, completion_tokens=1))
    return C


async def _run(script):
    ACTS.clear()
    agent.RFBClient = FakeRFB
    agent.AsyncOpenAI = _make_client(script)
    out, buf = {}, io.StringIO()
    with contextlib.redirect_stdout(buf):
        await agent.run_task("Tarea X", "wss://d/websockify", PASS, [], lambda e, r: out.update(e=e, r=r),
                             t_start=time.monotonic(), tag="t")
    return out, buf.getvalue()


async def main_extra():
    import httpx
    agent.CHAIN_PAUSE_S = 0.01
    req = httpx.Request("POST", "https://x/y")
    fin = lambda: ("ok", [tc(9, "finish", estado="terminada", resumen="listo")])

    # 1) un fallo transitorio (5xx) y luego responde: se recupera sin gastar un paso
    err5xx = agent.openai.InternalServerError("boom", response=httpx.Response(502, request=req), body=None)
    out, logs = await _run([err5xx, fin()])
    assert out["e"] == "terminada" and "fallo transitorio del modelo (1/3)" in logs, (out, logs)

    # 2) respuesta vacia (choices=[]) tambien se trata como transitoria
    out, logs = await _run(["EMPTY", fin()])
    assert out["e"] == "terminada" and "respuesta vacia del modelo" in logs, (out, logs)

    # 3) tres fallos seguidos: fallida, honesta, sin tocar mas el escritorio
    errc = agent.openai.APIConnectionError(request=req)
    out, logs = await _run([errc, errc, errc, fin()])
    assert out["e"] == "fallida" and "a medias" in out["r"] and ACTS == [], (out, logs)

    # 4) error permanente (401): no se reintenta
    err401 = agent.openai.AuthenticationError("no", response=httpx.Response(401, request=req), body=None)
    out, logs = await _run([err401, fin()])
    assert out["e"] == "fallida" and "fallo transitorio" not in logs, (out, logs)

    # 5) acciones encadenadas en una respuesta: se ejecutan en orden, y cada tool_call se responde
    chain = [tc(1, "click", x=10, y=20), tc(2, "type", text="abc"), tc(3, "click", x=30, y=40),
             tc(4, "type", text="def")]
    out, logs = await _run([("relleno", chain), fin()])
    assert out["e"] == "terminada", (out, logs)
    assert ACTS == [("click", 10, 20), ("type", "abc"), ("click", 30, 40), ("type", "def")], ACTS
    # 6) coordenadas mal formadas ("378, 275" en un solo campo) no rompen la accion
    out, logs = await _run([("clic", [tc(1, "click", x="378, 275")]), fin()])
    assert out["e"] == "terminada" and ACTS == [("click", 378, 275)], (out, ACTS, logs)
    out, logs = await _run([("clic", [tc(1, "click", x="100", y="200")]), fin()])
    assert ACTS == [("click", 100, 200)], ACTS

    # 7) numeros en respuesta: sin separador de miles; decimales y texto intactos
    assert agent.norm_respuesta("2.275.000") == "2275000"
    assert agent.norm_respuesta("2,275,000; 45") == "2275000; 45"
    assert agent.norm_respuesta("1.250") == "1.250" and agent.norm_respuesta("45") == "45"
    assert agent.norm_respuesta("3.5") == "3.5" and agent.norm_respuesta("lab-sync; (sin configurar)") == "lab-sync; (sin configurar)"
    out, logs = await _run([("fin", [tc(1, "finish", estado="terminada", resumen="total", respuesta="2.275.000")])])
    assert out["r"].endswith("respuesta: 2275000"), out

    # 8) el prompt lleva las reglas nuevas (secretos en pantalla, buscar exportar en menus)
    assert "claves de API" in agent.SYSTEM and "no lo copies" in agent.SYSTEM
    assert "Reportes" in agent.SYSTEM and "sin separadores de" in agent.SYSTEM
    print("EXTRA OK")


asyncio.run(main())
asyncio.run(main_extra())
