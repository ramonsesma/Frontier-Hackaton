"""Servicio HTTP con el contrato del reto: /health, POST /tasks, GET /tasks/{id}."""
import asyncio
import hmac
import os
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from agent import run_task

TEAM_TOKEN = os.environ.get("TEAM_TOKEN", "")

app = FastAPI()
TASKS: dict[str, dict] = {}
_bg: set = set()
_locks: dict[str, asyncio.Lock] = {}   # un bloqueo por escritorio (URL de VNC)
_live: dict[str, list[dict]] = {}      # tareas vivas (en curso o en cola) por escritorio


def _lock_for(key: str) -> asyncio.Lock:
    return _locks.setdefault(key, asyncio.Lock())


def auth(request: Request):
    header = request.headers.get("authorization", "")
    ok = header.startswith("Bearer ") and TEAM_TOKEN and hmac.compare_digest(
        header[7:].strip(), TEAM_TOKEN)
    if not ok:
        raise HTTPException(status_code=401, detail="no autorizado")


class Escritorio(BaseModel):
    url: str
    password: str


class TaskIn(BaseModel):
    tarea: str
    escritorio: Escritorio


@app.get("/health")
async def health(_=Depends(auth)):
    return {"ok": True}


@app.post("/tasks")
async def create_task(body: TaskIn, _=Depends(auth)):
    received = time.monotonic()  # el reloj de 5 min del evaluador corre desde aqui
    key = body.escritorio.url
    live = _live.setdefault(key, [])

    # Misma tarea sobre el mismo escritorio mientras sigue activa: es un reintento, no se repite.
    for t in live:
        if t["tarea"] == body.tarea and TASKS[t["id"]]["estado"] == "en_curso":
            return {"task_id": t["id"]}

    # Otra tarea sobre el mismo escritorio: el evaluador ya dejo de mirar la anterior,
    # asi que se cancela (cierra su VNC) para que no siga operando sobre la nueva.
    for t in list(live):
        t["atask"].cancel()

    task_id = uuid.uuid4().hex
    TASKS[task_id] = {"estado": "en_curso", "resumen": ""}
    entry = {"id": task_id, "tarea": body.tarea, "atask": None}

    def done(estado: str, resumen: str):
        TASKS[task_id] = {"estado": estado, "resumen": resumen}

    async def job():
        try:
            async with _lock_for(key):  # un solo agente a la vez por escritorio
                await run_task(body.tarea, body.escritorio.url, body.escritorio.password,
                               [TEAM_TOKEN, os.environ.get("OPENROUTER_API_KEY", "")], done,
                               t_start=received)
        except asyncio.CancelledError:
            TASKS[task_id] = {"estado": "fallida",
                              "resumen": "Tarea cancelada: llego otra tarea para el mismo escritorio."}
            raise

    def cleanup(_t):  # tambien cubre una tarea cancelada antes de llegar a ejecutarse
        _bg.discard(_t)
        if entry in live:
            live.remove(entry)
        if TASKS[task_id]["estado"] == "en_curso":
            TASKS[task_id] = {"estado": "fallida",
                              "resumen": "Tarea cancelada: llego otra tarea para el mismo escritorio."}

    entry["atask"] = asyncio.create_task(job())
    live.append(entry)
    _bg.add(entry["atask"])
    entry["atask"].add_done_callback(cleanup)
    return {"task_id": task_id}


@app.get("/tasks/{task_id}")
async def get_task(task_id: str, _=Depends(auth)):
    t = TASKS.get(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="tarea no encontrada")
    return t
