"""Pruebas del servicio sin red: reemplaza run_task por un agente falso con espera."""
import asyncio
import os
import time

os.environ["TEAM_TOKEN"] = "tok-prueba"

import httpx

import agent
import server

H = {"Authorization": "Bearer tok-prueba"}
LOG = []


async def fake_run_task(tarea, url, password, secrets, on_done, t_start=None, tag=""):
    LOG.append(("inicio", tarea, url))
    try:
        await asyncio.sleep(0.6)
        on_done("terminada", f"hecho {tarea}")
    except asyncio.CancelledError:
        LOG.append(("cancelada", tarea, url))
        await asyncio.sleep(0.1)  # cerrar VNC
        LOG.append(("cerrada", tarea, url))
        raise
    LOG.append(("fin", tarea, url))


def body(tarea, url):
    return {"tarea": tarea, "escritorio": {"url": url, "password": "pw"}}


async def estado(c, tid):
    return (await c.get(f"/tasks/{tid}", headers=H)).json()


async def main():
    server.run_task = fake_run_task
    t = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=t, base_url="http://x") as c:
        # 1. misma tarea y mismo escritorio mientras sigue activa -> mismo task_id
        a = (await c.post("/tasks", headers=H, json=body("A", "wss://d1"))).json()["task_id"]
        a2 = (await c.post("/tasks", headers=H, json=body("A", "wss://d1"))).json()["task_id"]
        assert a == a2, "duplicado exacto debe devolver el mismo task_id"

        # 2. otra tarea en el mismo escritorio -> cancela la anterior y espera a que cierre
        await asyncio.sleep(0.2)
        b = (await c.post("/tasks", headers=H, json=body("B", "wss://d1"))).json()["task_id"]
        assert b != a
        await asyncio.sleep(0.3)
        ea = await estado(c, a)
        assert ea["estado"] == "fallida" and "cancelada" in ea["resumen"], ea
        await asyncio.sleep(1.0)
        assert (await estado(c, b))["estado"] == "terminada"
        orden = [x[0] + ":" + x[1] for x in LOG if x[2] == "wss://d1"]
        assert orden.index("cerrada:A") < orden.index("inicio:B"), orden  # B no empieza antes de cerrar A

        # 3. escritorios distintos corren en paralelo
        LOG.clear()
        t0 = time.monotonic()
        x = (await c.post("/tasks", headers=H, json=body("X", "wss://d2"))).json()["task_id"]
        y = (await c.post("/tasks", headers=H, json=body("Y", "wss://d3"))).json()["task_id"]
        await asyncio.sleep(0.9)
        assert (await estado(c, x))["estado"] == "terminada"
        assert (await estado(c, y))["estado"] == "terminada"
        assert time.monotonic() - t0 < 1.4, "no deben ejecutarse en serie"

        # 4. misma tarea otra vez ya terminada -> tarea nueva (no es duplicado en curso)
        z = (await c.post("/tasks", headers=H, json=body("X", "wss://d2"))).json()["task_id"]
        assert z != x

        # 5. sin token
        r = await c.post("/tasks", json=body("Q", "wss://d9"))
        assert r.status_code == 401
        # 5b. token con caracteres no ASCII (p. ej. "…"): 401, nunca 500
        r = await c.get("/health", headers={"Authorization": "Bearer ftr_…".encode("utf-8")})
        assert r.status_code == 401, r.status_code
        await asyncio.sleep(0.8)

    # 6. agente real: si al llegar su turno ya no queda tiempo, no toca el escritorio
    class NoDebeUsarse:
        def __init__(self, *a, **k):
            raise AssertionError("no debia conectarse al escritorio")

    agent.RFBClient = NoDebeUsarse
    out = {}
    await agent.run_task("T", "wss://nada", "pw", [], lambda e, r: out.update(e=e, r=r),
                         t_start=time.monotonic() - 260)
    assert out["e"] == "fallida" and "ningun cambio" in out["r"], out
    print("SERVER OK")


asyncio.run(main())
