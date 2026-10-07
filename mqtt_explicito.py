"""
Lab 3 - Etapa 2: Cliente MQTT explícito hacia Azure IoT Central
=================================================================

Este script hace "a mano" lo que el SDK azure-iot-device hace por debajo:
  1. Genera un token SAS firmando con HMAC-SHA256 (mismo algoritmo del SDK).
  2. Se registra vía DPS usando MQTT explícito (no HTTP, no SDK).
  3. Recibe el hub asignado y se conecta a él por MQTT/TLS.
  4. Publica telemetría (3 variables) al topic de eventos del dispositivo.
  5. Se suscribe a comandos (C2D / direct methods) para poder observarlos.

Requiere:
    pip install paho-mqtt

Variables de entorno esperadas:
    ID_SCOPE          -> ID scope de tu app de IoT Central
    DEVICE_ID_MQTT    -> Device ID del dispositivo nuevo (registrado a mano en IoT Central)
    DEVICE_KEY_MQTT   -> Primary key de ESE dispositivo (ya derivada por IoT Central,
                          la ves en Devices -> tu-dispositivo -> Connect -> SAS-IoT-Devices)
"""

import os
import time
import json
import hmac
import hashlib
import base64
import urllib.parse
import ssl
import threading

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuración desde variables de entorno
# ---------------------------------------------------------------------------
ID_SCOPE = os.environ["ID_SCOPE"]
DEVICE_ID = os.environ["DEVICE_ID_MQTT"]
DEVICE_KEY = os.environ["DEVICE_KEY_MQTT"]

DPS_HOST = "global.azure-devices-provisioning.net"
DPS_API_VERSION = "2019-03-31"

PUBLISH_INTERVAL_SECS = 5  # mismo intervalo que tu script SDK, para comparar de igual a igual

# Evento para saber cuándo llegó la respuesta de DPS
dps_result = {}
dps_event = threading.Event()

# Evento para saber cuándo terminó el connect al Hub
hub_connected_event = threading.Event()


# ---------------------------------------------------------------------------
# Paso 1: Generación de token SAS (HMAC-SHA256), igual que hace el SDK
# ---------------------------------------------------------------------------
def generate_sas_token(resource_uri, key, expiry_secs=3600):
    """
    Genera un SharedAccessSignature token firmando resource_uri + expiry
    con la clave del dispositivo (HMAC-SHA256), tal como especifica
    la autenticación SAS de Azure IoT.
    """
    ttl = int(time.time() + expiry_secs)
    sign_key = f"{urllib.parse.quote_plus(resource_uri)}\n{ttl}"
    signature = base64.b64encode(
        hmac.new(base64.b64decode(key), sign_key.encode("utf-8"), hashlib.sha256).digest()
    )
    token = (
        f"SharedAccessSignature sr={urllib.parse.quote_plus(resource_uri)}"
        f"&sig={urllib.parse.quote_plus(signature)}&se={ttl}"
    )
    return token


# ---------------------------------------------------------------------------
# Paso 2: Registro vía DPS usando MQTT explícito
# ---------------------------------------------------------------------------
def provision_via_dps():
    """
    Se conecta a global.azure-devices-provisioning.net por MQTT/TLS,
    publica una solicitud de registro y espera la respuesta con el
    hub asignado (assigned_hub). Devuelve (assigned_hub, device_id).
    """
    resource_uri = f"{ID_SCOPE}/registrations/{DEVICE_ID}"
    sas_token = generate_sas_token(resource_uri, DEVICE_KEY)

    username = f"{ID_SCOPE}/registrations/{DEVICE_ID}/api-version={DPS_API_VERSION}"

    client = mqtt.Client(client_id=DEVICE_ID, protocol=mqtt.MQTTv311)
    client.username_pw_set(username=username, password=sas_token)
    client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)

    rid_counter = {"value": 1}

    def on_connect(c, userdata, flags, rc):
        print(f"[DPS] Conectado (rc={rc}). Suscribiendo a respuestas...")
        c.subscribe("$dps/registrations/res/#")
        # Publicamos la solicitud de registro con un request id (rid) propio
        c.publish(
            f"$dps/registrations/PUT/iotdps-register/?$rid={rid_counter['value']}",
            payload=json.dumps({"registrationId": DEVICE_ID}),
            qos=1,
        )
        print("[DPS] Solicitud de registro enviada.")

    def on_message(c, userdata, msg):
        print(f"[DPS] Mensaje recibido en topic: {msg.topic}")
        payload = json.loads(msg.payload.decode())
        dps_result["payload"] = payload
        dps_result["topic"] = msg.topic
        dps_event.set()

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(DPS_HOST, port=8883, keepalive=30)
    client.loop_start()

    # Esperamos hasta 15 segundos por la primera respuesta de DPS
    dps_event.wait(timeout=15)

    if "payload" not in dps_result:
        client.loop_stop()
        client.disconnect()
        raise RuntimeError("No se recibió respuesta de DPS a tiempo.")

    payload = dps_result["payload"]
    status = payload.get("status")
    print(f"[DPS] Status recibido: {status}")

    # DPS normalmente responde 202/"assigning" primero, con un operationId.
    # Hay que hacer polling a $dps/registrations/GET/iotdps-get-operationstatus
    # hasta que el status cambie a "assigned" (o "failed").
    max_polls = 10
    poll_count = 0
    while status == "assigning" and poll_count < max_polls:
        operation_id = payload["operationId"]
        wait_secs = 3  # podrías leer retry-after del topic si quieres ser más preciso
        print(f"[DPS] Aún 'assigning', esperando {wait_secs}s antes de reintentar "
              f"(operationId={operation_id})...")
        time.sleep(wait_secs)

        dps_event.clear()
        rid_counter["value"] += 1
        poll_topic = (
            f"$dps/registrations/GET/iotdps-get-operationstatus/"
            f"?$rid={rid_counter['value']}&operationId={operation_id}"
        )
        client.publish(poll_topic, payload="", qos=1)

        dps_event.wait(timeout=15)
        if "payload" not in dps_result:
            client.loop_stop()
            client.disconnect()
            raise RuntimeError("No se recibió respuesta de DPS durante el polling.")

        payload = dps_result["payload"]
        status = payload.get("status")
        print(f"[DPS] Status recibido: {status}")
        poll_count += 1

    client.loop_stop()
    client.disconnect()

    if status != "assigned":
        raise RuntimeError(
            f"DPS no llegó a 'assigned' tras {poll_count} intentos "
            f"(último status='{status}'). Respuesta completa: {payload}"
        )

    assigned_hub = payload["registrationState"]["assignedHub"]
    device_id = payload["registrationState"]["deviceId"]
    print(f"[DPS] Hub asignado: {assigned_hub}")
    return assigned_hub, device_id


# ---------------------------------------------------------------------------
# Paso 3: Conexión al IoT Hub y publicación de telemetría
# ---------------------------------------------------------------------------
def connect_to_hub(assigned_hub, device_id):
    """
    Se conecta al IoT Hub asignado usando MQTT/TLS explícito y devuelve
    el cliente ya conectado, listo para publicar y recibir comandos.
    """
    resource_uri = f"{assigned_hub}/devices/{device_id}"
    sas_token = generate_sas_token(resource_uri, DEVICE_KEY)

    # api-version fija la versión del protocolo del Hub que hablamos
    username = f"{assigned_hub}/{device_id}/?api-version=2021-04-12"

    client = mqtt.Client(client_id=device_id, protocol=mqtt.MQTTv311)
    client.username_pw_set(username=username, password=sas_token)
    client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)

    def on_connect(c, userdata, flags, rc):
        print(f"[HUB] Conectado a {assigned_hub} (rc={rc})")
        # Topic de comandos / direct methods
        c.subscribe(f"$iothub/methods/POST/#")
        # Topic de comandos cloud-to-device (C2D)
        c.subscribe(f"devices/{device_id}/messages/devicebound/#")
        hub_connected_event.set()

    def on_message(c, userdata, msg):
        print(f"[HUB] Comando/mensaje recibido en {msg.topic}: {msg.payload}")
        # Si es un direct method, respondemos 200 para que Central no marque error
        if msg.topic.startswith("$iothub/methods/POST/"):
            rid = msg.topic.split("$rid=")[-1]
            response_topic = f"$iothub/methods/res/200/?$rid={rid}"
            c.publish(response_topic, payload=json.dumps({"result": "ok"}))

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(assigned_hub, port=8883, keepalive=60)
    client.loop_start()

    hub_connected_event.wait(timeout=15)
    if not hub_connected_event.is_set():
        raise RuntimeError("No se pudo conectar al Hub a tiempo.")

    return client


def publish_telemetry_loop(client, device_id, qos=1):
    """
    Publica las 3 variables periódicamente al topic de eventos del dispositivo,
    con el QoS indicado. Imprime tamaño de payload y timestamp para las
    mediciones de la Etapa 3.
    """
    topic = f"devices/{device_id}/messages/events/"

    while True:
        payload = {
            "HeartRate": __import__("random").randint(60, 100),
            "Temperature": round(__import__("random").uniform(36.0, 38.0), 2),
            "SPO2": __import__("random").randint(90, 100),
        }
        body = json.dumps(payload)
        size_bytes = len(body.encode("utf-8"))
        t_publish = time.time()

        result = client.publish(topic, payload=body, qos=qos)
        result.wait_for_publish(timeout=5)

        print(
            f"[PUBLISH] t={t_publish:.3f} qos={qos} size={size_bytes}B "
            f"payload={body}"
        )
        time.sleep(PUBLISH_INTERVAL_SECS)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=== Etapa 2: Cliente MQTT explícito hacia IoT Central ===")
    print(f"ID_SCOPE={ID_SCOPE}  DEVICE_ID={DEVICE_ID}")

    assigned_hub, device_id = provision_via_dps()
    client = connect_to_hub(assigned_hub, device_id)

    print("Conectado a IoT Central por MQTT explícito ✅")
    print(f"Publicando cada {PUBLISH_INTERVAL_SECS}s en devices/{device_id}/messages/events/")

    try:
        # Cambia qos=1 por qos=0 o qos=2 aquí para las pruebas de la Etapa 3
        publish_telemetry_loop(client, device_id, qos=2)
    except KeyboardInterrupt:
        print("\nDeteniendo cliente...")
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
