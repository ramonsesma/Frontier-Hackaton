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


asyncio.run(main())
