"""
Bloque 3: Enviar telemetria a IoT Central por HTTPS (REST).
Lee el assignedHub de assigned_hub.txt y manda HeartRate, Temperature, SPO2.
"""

import base64, hashlib, hmac, time, urllib.parse, json, random, requests

DEVICE_ID = "b7oz89ybow"
PRIMARY_KEY = "AIYdC/1gW1XF/HU2GVvKIbi6WgtQahij8lTZ0WblZsU="
API_VERSION = "2021-04-12"


def build_sas_token(resource_uri, primary_key_b64, ttl_seconds=3600):
    expiry = int(time.time()) + ttl_seconds
    uri_encoded = urllib.parse.quote(resource_uri, safe="")
    string_to_sign = f"{uri_encoded}\n{expiry}"
    key_bytes = base64.b64decode(primary_key_b64)
    signature = hmac.new(key_bytes, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    signature_encoded = urllib.parse.quote(base64.b64encode(signature).decode("utf-8"), safe="")
    return f"SharedAccessSignature sr={uri_encoded}&sig={signature_encoded}&se={expiry}"


def send_telemetry(hub, datos):
    # 1. Token SAS con el recurso del HUB (no el de DPS)
    resource = f"{hub}/devices/{DEVICE_ID}"
    token = build_sas_token(resource, PRIMARY_KEY)

    # 2. URL del endpoint de telemetria
    url = f"https://{hub}/devices/{DEVICE_ID}/messages/events?api-version={API_VERSION}"

    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "lab4-http/1.0",
    }

    # 3. POST con el JSON de las 3 variables
    r = requests.post(url, headers=headers, data=json.dumps(datos), timeout=30)

    print(f"Enviado: {datos}")
    print(f"  HTTP {r.status_code}  (204 = exito)")
    if r.status_code != 204:
        print(f"  Respuesta: {r.text[:300]}")
    return r.status_code


if __name__ == "__main__":
    # Leer el hub guardado en el bloque anterior
    with open("assigned_hub.txt", "r") as f:
        hub = f.read().strip()
    print(f"Usando hub: {hub}\n")

    # Enviar 5 mensajes con las 3 variables, uno cada 5 segundos
    for i in range(1, 6):
        datos = {
            "HeartRate": random.randint(60, 100),
            "Temperature": round(random.uniform(36.0, 38.5), 2),
            "SPO2": random.randint(94, 100),
        }
        print(f"--- Mensaje {i} ---")
        send_telemetry(hub, datos)
        if i < 5:
            time.sleep(5)

    print("\nListo. Revisa IoT Central -> Devices -> amqp-lab4 -> Raw data")