# Sistema Distribuido de Consulta y Registro de Información

Implementación funcional de la arquitectura descrita en la Guía 2 - S3
(Comunicación en Sistemas Distribuidos). Simula dos servidores (Servidor 1 /
Ubuntu Server y Servidor 2 / Rocky Linux) como contenedores Docker en la
misma red, cada uno con su propia base de datos MariaDB, sincronizados entre
sí por WebSocket con heartbeat y cola de pendientes, detrás de un proxy
inverso Nginx.

## Una corrección respecto al documento original

El documento usa las IPs `198.168.100.10` / `198.168.100.20`. `198.168.0.0/16`
no es un rango privado (RFC 1918); lo más probable es que sea un error de
tipeo por `192.168.0.0/16`. En esta implementación se usó **192.168.100.10** y
**192.168.100.20**, que sí es un rango válido para una red interna. Si
necesitas conservar exactamente `198.168.x.x` para la entrega, basta con
cambiar el valor en `docker-compose.yml`.

Los puertos sí respetan exactamente el diagrama: la API HTTP corre en el
**8000** y el servicio de cliente/servidor WebSocket de sincronización corre
por separado en el **8765**, cada uno como su propio proceso dentro del
mismo contenedor (ambos arrancan juntos al iniciar `server1`/`server2`).

## Estructura

```
distribuidos/
├── docker-compose.yml       # Orquesta db1, db2, server1, server2 y nginx
├── nginx/
│   └── nginx.conf           # Proxy inverso / balanceo entre server1 y server2
└── server/                  # Código compartido por ambos "servidores"
    ├── Dockerfile
    ├── requirements.txt
    └── app/
        ├── main.py          # Endpoints HTTP: /registro, /consulta, /health
        ├── database.py      # Conexión SQLAlchemy a la MariaDB local
        ├── models.py        # Tablas: registros, cola_pendientes
        ├── schemas.py       # Validación de entrada/salida (Pydantic)
        └── sync.py          # Cliente/servidor WebSocket (puerto 8765), heartbeat, cola
```

Cada contenedor `server1` / `server2` corre exactamente el mismo código; lo
que cambia es la configuración por variables de entorno (a cuál base de
datos local se conecta y cuál es la dirección de su par).

## Cómo se comunican los componentes (resumen)

- **Cliente → Nginx**: HTTP/HTTPS en el puerto 8080 (host) / 80 (contenedor).
  Nginx es el punto único de entrada y reparte las peticiones entre
  `server1` y `server2`.
- **Nginx → server1/server2**: HTTP en el puerto 8000.
- **server1/server2 → su MariaDB local**: TCP/3306, solo tráfico dentro del
  contenedor del propio nodo (`db1` para `server1`, `db2` para `server2`).
- **server1 ↔ server2**: WebSocket en el puerto **8765** (`ws://server2:8765`
  y `ws://server1:8765`), persistente y bidireccional, usado para
  (a) heartbeat cada 10s que indica que el nodo par sigue vivo, y
  (b) mensajes `sync` con cada registro nuevo. Es un servicio aparte de la
  API HTTP (que sigue en el 8000), tal como en el diagrama original.
- **Cola de pendientes**: si al crear un registro el par no está conectado,
  el registro se guarda igual localmente y además se anota en
  `cola_pendientes`. Cuando la conexión WebSocket se restablece, esos
  registros se reenvían automáticamente y se retiran de la cola.

## Cómo ejecutarlo

```bash
cd distribuidos
docker compose up --build
```

Espera a que ambas bases de datos pasen su healthcheck; verás en los logs
`[server1] Conectado al par en ws://server2:8000/ws/sync` (y su equivalente
en server2) cuando el canal de sincronización quede establecido.

## Cliente web

Con todo levantado, abre en el navegador:

```
http://localhost:8080/ui/
```

Es un panel de una sola página (sin frameworks, HTML+JS plano) que cubre los
7 requisitos de la guía:

| # | Requisito | Dónde está en el cliente |
|---|---|---|
| 1 | Registrar información desde un cliente | Formulario "Registrar información" |
| 2 | Enviar la información al servidor correspondiente | Nginx reparte cada envío entre server1/server2 |
| 3 | Consultar información almacenada | Tabla "Consultar información almacenada" |
| 4 | Comunicación entre los dos servidores | Canal WebSocket 8765 (ver `sync.py`) |
| 5 | Identificar cuál servidor atiende la solicitud | Cada respuesta trae el header `X-Atendido-Por`; se muestra como badge de color junto a "Última solicitud atendida por…" y en la columna "Origen" de la tabla |
| 6 | Mostrar el estado de conexión de cada servidor | Panel derecho: dos tarjetas que consultan `/status/server1` y `/status/server2` cada 4s, cada una directo a su nodo (no balanceado), mostrando si está en línea y si su canal de sincronización con el par está activo |
| 7 | Seguir funcionando aunque el cliente hable con servidores distintos | Como los datos se replican por WebSocket, no importa a cuál nodo te conecte Nginx en cada request: ves la misma información |

Server 1 se identifica en color ámbar y Server 2 en cian en todo el cliente
(badges, tabla, tarjetas de estado), para que sea visualmente claro cuál nodo
originó o atendió cada cosa.

### Endpoints nuevos usados por el cliente

- `GET /status/server1` y `GET /status/server2`: consultan el `/health` de
  cada nodo **directamente**, sin pasar por el balanceador de Nginx. Así el
  panel puede mostrar el estado real de ambos aunque uno esté caído.
- Header `X-Atendido-Por` en **toda** respuesta de la API: indica qué
  servidor procesó esa solicitud puntual (útil sobre todo en `/consulta`,
  donde los datos mostrados pueden venir originalmente del otro nodo, pero
  quien respondió es el que Nginx eligió para esa petición).

## Probarlo por API (sin el cliente web)

Crear un registro (Nginx reparte entre server1 y server2 según le toque):

```bash
curl -X POST http://localhost:8080/registro \
  -H "Content-Type: application/json" \
  -d '{"contenido": "Primer registro de prueba"}'
```

Consultar todos los registros (deberías ver el mismo registro
independientemente de a cuál nodo golpee la petición, porque ya se
sincronizó):

```bash
curl http://localhost:8080/consulta
```

Ver el estado de salud y si el nodo tiene contacto con su par:

```bash
curl http://localhost:8080/health
```

### Probar tolerancia a fallos y la cola de pendientes

```bash
docker compose stop server2          # simula la caída del nodo 2
curl -X POST http://localhost:8080/registro \
  -H "Content-Type: application/json" \
  -d '{"contenido": "Registro creado con server2 caído"}'
# este registro queda en server1 y en su cola_pendientes

docker compose start server2         # server2 vuelve
# unos segundos después, server1 detecta la reconexión y vacía su cola:
# el registro aparece también en server2 sin intervención manual
```

## Relación con las preguntas orientadoras de la guía

- **Disponibilidad ante fallo de un nodo**: si un servidor cae, el otro
  sigue respondiendo consultas y aceptando registros nuevos (aunque Nginx,
  en este demo, seguirá intentando enviarle tráfico al caído; en producción
  se añadiría un healthcheck activo en el `upstream` de Nginx).
- **Coherencia eventual**: no hay bloqueo ni consenso fuerte entre los dos
  nodos; cada uno acepta escrituras locales de inmediato y propaga el
  cambio de forma asíncrona. Es un modelo de consistencia eventual, no de
  consistencia fuerte.
- **Recuperación tras fallo**: la cola de pendientes es justamente el
  mecanismo que evita perder datos escritos mientras el otro nodo estaba
  inalcanzable.
