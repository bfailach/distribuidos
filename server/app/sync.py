import asyncio
import json
import os
from datetime import datetime

import websockets
from websockets.exceptions import ConnectionClosed

SERVER_ID = os.getenv("SERVER_ID", "server1")
PEER_HOST = os.getenv("PEER_HOST", "server2")
WS_SYNC_PORT = int(os.getenv("WS_SYNC_PORT", "8765"))
PEER_WS_URL = f"ws://{PEER_HOST}:{WS_SYNC_PORT}"
RECONNECT_DELAY = int(os.getenv("SYNC_RECONNECT_DELAY", "5"))
HEARTBEAT_INTERVAL = int(os.getenv("SYNC_HEARTBEAT_INTERVAL", "10"))

# Estado global simple del canal saliente hacia el par
peer_connected = False
_outbound_ws = None


async def client_loop():
    """Tarea de fondo: mantiene una conexión WebSocket persistente y
    bidireccional hacia el servidor par. Si se cae, reintenta cada
    RECONNECT_DELAY segundos (esto es lo que permite detectar la caída
    del otro nodo y recuperarse solo cuando vuelve)."""
    global peer_connected, _outbound_ws

    while True:
        try:
            async with websockets.connect(PEER_WS_URL) as ws:
                _outbound_ws = ws
                peer_connected = True
                print(f"[{SERVER_ID}] Conectado al par en {PEER_WS_URL}")

                await _flush_cola_pendiente()

                heartbeat_task = asyncio.create_task(_heartbeat(ws))
                try:
                    async for _ in ws:
                        pass  # este canal es solo saliente; las llegadas se atienden en /ws/sync
                finally:
                    heartbeat_task.cancel()
        except (ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
            print(f"[{SERVER_ID}] Sin conexión con el par ({exc}); "
                  f"reintentando en {RECONNECT_DELAY}s")
        finally:
            peer_connected = False
            _outbound_ws = None

        await asyncio.sleep(RECONNECT_DELAY)


async def _heartbeat(ws):
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        await ws.send(json.dumps({"type": "heartbeat", "origen": SERVER_ID}))


async def enviar_registro(registro) -> bool:
    """Intenta enviar un registro recién creado al servidor par.
    Devuelve True si se pudo enviar, False si el par no está disponible
    (en cuyo caso el llamador debe encolarlo en cola_pendientes)."""
    if not peer_connected or _outbound_ws is None:
        return False
    try:
        await _outbound_ws.send(json.dumps({
            "type": "sync",
            "data": {
                "id": registro.id,
                "contenido": registro.contenido,
                "origen_servidor": registro.origen_servidor,
                "timestamp": registro.timestamp.isoformat(),
            },
        }))
        return True
    except ConnectionClosed:
        return False


async def _flush_cola_pendiente():
    """Al reconectar con el par, reenvía todo lo que quedó pendiente
    mientras estuvo caído."""
    from .database import SessionLocal
    from .models import Registro, ColaPendiente

    db = SessionLocal()
    try:
        pendientes = db.query(ColaPendiente).all()
        if pendientes:
            print(f"[{SERVER_ID}] Vaciando cola de pendientes "
                  f"({len(pendientes)} registros)")
        for p in pendientes:
            registro = db.query(Registro).filter(Registro.id == p.registro_id).first()
            if registro and await enviar_registro(registro):
                db.delete(p)
        db.commit()
    finally:
        db.close()


async def _manejar_conexion_entrante(websocket):
    """Lado servidor del canal WebSocket dedicado (puerto 8765): recibe
    heartbeats y mensajes de sincronización que envía el par, e inserta
    localmente los registros que aún no existan (idempotente por id)."""
    from .database import SessionLocal
    from .models import Registro

    try:
        async for raw in websocket:
            msg = json.loads(raw)

            if msg.get("type") == "heartbeat":
                continue

            if msg.get("type") == "sync":
                data = msg["data"]
                db = SessionLocal()
                try:
                    existente = db.query(Registro).filter(
                        Registro.id == data["id"]
                    ).first()
                    if not existente:
                        db.add(Registro(
                            id=data["id"],
                            contenido=data["contenido"],
                            origen_servidor=data["origen_servidor"],
                            timestamp=datetime.fromisoformat(data["timestamp"]),
                        ))
                        db.commit()
                        print(f"[{SERVER_ID}] Registro {data['id']} "
                              f"sincronizado desde {data['origen_servidor']}")
                finally:
                    db.close()
    except ConnectionClosed:
        # Conexión cerrada por el par; client_loop se encargará de reconectar
        pass


async def start_server():
    """Levanta el servidor WebSocket dedicado en el puerto 8765, tal como
    lo indica el diagrama de arquitectura: un servicio de cliente/servidor
    WebSocket separado del puerto HTTP (8000) de la API."""
    server = await websockets.serve(_manejar_conexion_entrante, "0.0.0.0", WS_SYNC_PORT)
    print(f"[{SERVER_ID}] Servidor WebSocket de sincronización escuchando "
          f"en el puerto {WS_SYNC_PORT}")
    return server
