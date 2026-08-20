import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List

from fastapi import FastAPI, HTTPException

from .database import Base, engine, SessionLocal
from .models import Registro, ColaPendiente
from .schemas import RegistroCreate, RegistroOut
from . import sync

SERVER_ID = os.getenv("SERVER_ID", "server1")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    # Servidor WebSocket dedicado en el puerto 8765 (recibe sync/heartbeat del par)
    ws_server = await sync.start_server()
    # Cliente WebSocket que se conecta de forma persistente al par y reintenta si cae
    client_task = asyncio.create_task(sync.client_loop())

    yield

    client_task.cancel()
    ws_server.close()
    await ws_server.wait_closed()


app = FastAPI(title=f"API distribuida - {SERVER_ID}", lifespan=lifespan)


@app.middleware("http")
async def marcar_servidor_que_atiende(request, call_next):
    """Requisito 5: toda respuesta lleva un header indicando qué servidor
    (server1 o server2) atendió esta solicitud en particular, sin importar
    si el dato mostrado se originó en el otro nodo."""
    response = await call_next(request)
    response.headers["X-Atendido-Por"] = SERVER_ID
    return response


@app.get("/health")
def health():
    return {
        "server": SERVER_ID,
        "status": "ok",
        "peer_connected": sync.peer_connected,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.post("/registro", response_model=RegistroOut)
async def crear_registro(payload: RegistroCreate):
    db = SessionLocal()
    try:
        nuevo = Registro(
            id=str(uuid.uuid4()),
            contenido=payload.contenido,
            origen_servidor=SERVER_ID,
            timestamp=datetime.utcnow(),
        )
        db.add(nuevo)
        db.commit()
        db.refresh(nuevo)

        enviado = await sync.enviar_registro(nuevo)
        if not enviado:
            db.add(ColaPendiente(registro_id=nuevo.id, creado_en=datetime.utcnow()))
            db.commit()

        return nuevo
    finally:
        db.close()


@app.get("/consulta", response_model=List[RegistroOut])
def listar_registros():
    db = SessionLocal()
    try:
        return db.query(Registro).order_by(Registro.timestamp.desc()).all()
    finally:
        db.close()


@app.get("/consulta/{registro_id}", response_model=RegistroOut)
def obtener_registro(registro_id: str):
    db = SessionLocal()
    try:
        reg = db.query(Registro).filter(Registro.id == registro_id).first()
        if not reg:
            raise HTTPException(status_code=404, detail="Registro no encontrado")
        return reg
    finally:
        db.close()
