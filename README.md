# Laboratorio4_IoT

## MQTT + AMQP + tercer protocolo

UNAB

Comparación de MQTT, AMQP y un tercer protocolo (HTTP/HTTPS) para enviar telemetría de un **sensor de calidad de paciente** hacia Azure IoT Central.


## 1. Arquitectura

Con autorización del profesor, la ruta AMQP es `script → broker → VM → IoT Central`.

```
Localmente se ejecuta en WSL un publisher.js que envia la telemetria a un Broker de Docker compose creado con Artemis, donde los mensajes de telemetria son recibidos y guardados en la fila ANYCAST del Broker, luego de lo cual una VM accede mediante un tunel SSH inverso al Broker para recibir los mensajes de telemetria y mediante un bridge.js de amqp con puerto 5671 se envia la telemetria a el dispositivo de IoT central.

```

| Componente | Dónde corre | Función |
|---|---|---|
| `amqp/publisher.js` | WSL (VS Code) | Genera lecturas simuladas y las publica en la cola `telemetria` |
| Apache ActiveMQ Artemis | Docker Desktop (WSL) | Broker AMQP 1.0 local con cola durable |
| Túnel SSH inverso | WSL → VM | Expone el broker local en `127.0.0.1:5672` de la VM, sin abrirlo a Internet |
| `vm/bridge.js` | VM Azure | Consume de la cola y reenvía a IoT Central por AMQP |
| Azure IoT Central | Nube | Destino final; el dato se ve en *Raw data* / *Data explorer* |

### Variables de telemetría

| Variable | Rango simulado |
|---|---|
| `HeartRate` | 60 – 110 |
| `SPO2` | 92 – 100 |
| `Temperature` | 36 – 38.5 |

---


## 2. Requisitos

**Equipo local (WSL):**
- Windows con WSL2 y Docker Desktop (integración con WSL activada)
- Node.js ≥ 20.6 (se usa `--env-file`)
- Cliente `ssh` y la llave `.pem` de la VM en `~/.ssh` con permisos `600`

**VM de Azure (Linux):**
- Node.js ≥ 20.6
- Salida hacia `*.azure-devices.net` por el puerto **5671** (verificado) y `global.azure-devices-provisioning.net`
- Un dispositivo en IoT Central creado con la plantilla del Lab 3 (por ejemplo `paciente-amqp`)

---

## 3. Configuración

### 3.1 Variables de entorno


`.env` en WSL (raíz del repo):
```
BROKER_PASS=<clave del broker>
```

`.env` en la VM (`~/lab4/.env`, con `chmod 600`):
```
ID_SCOPE=<ID scope de IoT Central>
DEVICE_ID=<ID del dispositivo, p. ej. paciente-amqp>
DEVICE_KEY=<Primary key del dispositivo>
BROKER_PASS=<la misma clave del broker>
```

`ID_SCOPE`, `DEVICE_ID` y `DEVICE_KEY` se copian **del mismo** cuadro *Connect* del dispositivo en IoT Central. Si no pertenecen al mismo dispositivo, DPS responde `401 Unauthorized`.

Nota: Debido a las protecciones de GitHub con los datos sensibles, las variables se mantendran ocultas en el contenido escrito.

### 3.2 Dependencias

En la raíz (WSL):
```bash
npm install
```

En la VM (carpeta `~/lab4`):
```bash
npm i rhea azure-iot-device azure-iot-device-amqp \
  azure-iot-provisioning-device azure-iot-provisioning-device-http \
  azure-iot-security-symmetric-key
```

---

## 4. Cómo reproducirlo

Orden de arranque: **broker → cola → publicador → túnel → puente**.

### Paso 1 · Levantar el broker (WSL)

```bash
docker compose up -d
docker logs broker-amqp | tail -20     # buscar "Server is now active"
```

Consola web: <http://localhost:8161/console> (usuario `lab4`, clave `BROKER_PASS`).

### Paso 2 · Crear la cola `telemetria` (ANYCAST y durable)

Si la dirección ya existía como MULTICAST, bórrala primero:

```bash
docker exec broker-amqp sh -c '/var/lib/artemis-instance/bin/artemis address delete --name telemetria --force --user lab4 --password "$ARTEMIS_PASSWORD"'
```

Crea la cola explícita:

```bash
docker exec broker-amqp sh -c '/var/lib/artemis-instance/bin/artemis queue create --name telemetria --address telemetria --anycast --durable --auto-create-address --silent --user lab4 --password "$ARTEMIS_PASSWORD"'
```

> Una dirección MULTICAST descarta los mensajes si no hay suscriptores. Con ANYCAST el broker los retiene hasta que un consumidor los lea.

### Paso 3 · Ejecutar el publicador (WSL)

```bash
npm run publisher | tee evidencias/amqp/publisher.log
```

Cada mensaje publicado debe ir seguido de un `ack del broker`. En la consola de Artemis (*addresses → telemetria → queues → anycast → telemetria*) el `Message count` aumenta mientras no haya consumidor.

### Paso 4 · Abrir el túnel SSH inverso (segunda terminal de WSL)

```bash
ssh -N -R 5672:localhost:5672 \
  -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
  -i ~/.ssh/<llave>.pem azureuser@<IP_DE_LA_VM>
```

Verificación desde la VM:
```bash
ss -ltn | grep 5672      # <- debe mostrar 127.0.0.1:5672 en LISTEN
```

El puerto solo escucha en el loopback de la VM, por lo que el broker no queda expuesto a Internet.

### Paso 5 · Ejecutar el puente (VM)

Copia el script 'bridge.js' desde WSL:
```bash
scp -i ~/.ssh/<llave>.pem vm/bridge.js azureuser@<IP_DE_LA_VM>:~/lab4/
```

En la VM:
```bash
cd ~/lab4
node --env-file=.env bridge.js 2>&1 | tee bridge.log
```

Salida esperada:
```
DPS OK, hub asignado: <hub>.azure-devices.net
AMQP abierto con <hub>.azure-devices.net
conectado al broker 127.0.0.1:5672
<- broker {...}
-> Central (AMQP) OK
```

**Qué hace el puente:**
1. Registra el dispositivo en DPS (por HTTP, ver notas) y obtiene el hub asignado.
2. Abre una conexión **AMQP** con el hub (puerto 5671, TLS).
3. Consume de la cola `telemetria` con control de flujo (`credit_window: 10`) y confirmación manual.
4. Reenvía cada mensaje a IoT Central y **solo confirma (`accept`) al broker cuando Central lo acepta**; si falla, hace `release` y el mensaje vuelve a la cola.

### Paso 6 · Prueba de cola durable (ventaja de AMQP)

1. Con el publicador y el túnel activos, detén el puente (`Ctrl+C`).
2. Espera unos 10 mensajes (~50 s) y captura el `Message count` en Artemis.
3. Reinicia el puente:
   ```bash
   node --env-file=.env bridge.js 2>&1 | tee bridge-recuperacion.log
   ```
4. Captura el contador bajando a 0 y la ráfaga de mensajes en *Raw data*.

Resultado: el broker retuvo los mensajes mientras el consumidor estaba caído y se entregaron todos al volver. La entrega es **at-least-once**: tras un corte brusco podría haber duplicados, las evidencias de este proceso pueden ser vistas en las imagenes E16-E19 de evidencias/amqp.

---

## 5. Notas técnicas y hallazgos

- **ANYCAST vs MULTICAST:** una dirección creada automáticamente por un cliente AMQP sin indicar el tipo queda como MULTICAST y descarta mensajes sin suscriptores. El publicador y el puente piden la cola con `capabilities: ['queue']`.
- **DPS por AMQP en bucle:** con credenciales inválidas, el registro por AMQP devuelve `amqp:internal-error`; el SDK lo trata como reintentable y repite cada 2 s sin mostrar error, por lo que el script parecía congelado. Con DPS por **HTTP** el fallo se ve de inmediato (`401000 Unauthorized`). Por eso el puente registra por HTTP y envía la telemetría por **AMQP**.
- **Puerto y TLS:** conexión a IoT Central por AMQP en el puerto **5671** con TLS (no fue necesario AMQP sobre WebSockets). El tráfico local `publisher → broker` es AMQP 1.0 sin TLS, solo dentro de la máquina.
- **El SDK de Python no se usó:** `azure-iot-device` para Python no ofrece AMQP hacia IoT Hub; el SDK de Node.js sí, por eso el puente está en Node.

---
