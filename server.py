"""Servicio HTTP con el contrato del reto: /health, POST /tasks, GET /tasks/{id}."""
import asyncio
import hmac
import os
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from agent import run_task

TEAM_TOKEN = os.environ.get("TEAM_TOKEN", "")

app = FastAPI()
TASKS: dict[str, dict] = {}
_bg: set = set()
_lock = asyncio.Lock()  # un escritorio a la vez: evita acciones cruzadas entre tareas


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
    task_id = uuid.uuid4().hex
    TASKS[task_id] = {"estado": "en_curso", "resumen": ""}

    def done(estado: str, resumen: str):
        TASKS[task_id] = {"estado": estado, "resumen": resumen}

    async def job():
        async with _lock:
            await run_task(body.tarea, body.escritorio.url, body.escritorio.password,
                           [TEAM_TOKEN, os.environ.get("ANTHROPIC_API_KEY", "")], done)

    t = asyncio.create_task(job())
    _bg.add(t)
    t.add_done_callback(_bg.discard)
    return {"task_id": task_id}


@app.get("/tasks/{task_id}")
async def get_task(task_id: str, _=Depends(auth)):
    t = TASKS.get(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="tarea no encontrada")
    return t
