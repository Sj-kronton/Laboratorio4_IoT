
import base64, hashlib, hmac, time, urllib.parse, requests

ID_SCOPE = "0ne00FE096E"
DEVICE_ID = "b7oz89ybow"
PRIMARY_KEY = "AIYdC/1gW1XF/HU2GVvKIbi6WgtQahij8lTZ0WblZsU="

DPS_HOST = "global.azure-devices-provisioning.net"
API_VERSION = "2021-06-01"


def build_sas_token(resource_uri, primary_key_b64, ttl_seconds=3600):
    expiry = int(time.time()) + ttl_seconds
    uri_encoded = urllib.parse.quote(resource_uri, safe="")
    string_to_sign = f"{uri_encoded}\n{expiry}"
    key_bytes = base64.b64decode(primary_key_b64)
    signature = hmac.new(key_bytes, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    signature_encoded = urllib.parse.quote(base64.b64encode(signature).decode("utf-8"), safe="")
    return f"SharedAccessSignature sr={uri_encoded}&sig={signature_encoded}&se={expiry}"


def register_device():
    resource = f"{ID_SCOPE}/registrations/{DEVICE_ID}"
    token = build_sas_token(resource, PRIMARY_KEY)
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "lab4-http/1.0",
    }

    # ---------- PASO 1: lanzar el registro (PUT) ----------
    register_url = f"https://{DPS_HOST}/{ID_SCOPE}/registrations/{DEVICE_ID}/register?api-version={API_VERSION}"
    body = {"registrationId": DEVICE_ID}

    print("PASO 1 - Lanzando registro (PUT)...")
    r = requests.put(register_url, headers=headers, json=body, timeout=30)
    print("HTTP status:", r.status_code)
    print("Respuesta:", r.text[:500])

    if r.status_code not in (200, 202):
        print("ERROR: DPS devolvio status inesperado en el PUT:", r.status_code)
        return None

    data = r.json()

    # Si ya viene "assigned" en la misma respuesta, genial
    if data.get("status") == "assigned":
        hub = data["registrationState"]["assignedHub"]
        print(f"\n>>> ASSIGNED HUB (directo): {hub}")
        return hub

    operation_id = data.get("operationId")
    if not operation_id:
        print("ERROR: DPS no devolvio operationId.")
        return None
    print(f"operationId = {operation_id}")

    # ---------- PASO 2: consultar estado (GET) ----------
    status_url = (
        f"https://{DPS_HOST}/{ID_SCOPE}/registrations/{DEVICE_ID}"
        f"/operations/{operation_id}?api-version={API_VERSION}"
    )

    for intento in range(1, 31):
        print(f"\n--- Consulta {intento} ---")
        r = requests.get(status_url, headers=headers, timeout=30)
        print("HTTP status:", r.status_code)
        print("Respuesta:", r.text[:500])

        if r.status_code not in (200, 202):
            print("ERROR: DPS devolvio status inesperado en el GET:", r.status_code)
            return None

        data = r.json()
        status = data.get("status")

        if status == "assigned":
            hub = data["registrationState"]["assignedHub"]
            print(f"\n>>> ASSIGNED HUB: {hub}")
            return hub

        if status == "failed":
            err = data.get("registrationState", {}).get("errorMessage", "sin detalle")
            print(f"\n>>> REGISTRO FALLIDO: {err}")
            return None

        print(f"Estado: {status}. Esperando 3s...")
        time.sleep(3)

    print("Se agoto el numero de intentos sin obtener 'assigned'.")
    return None


if __name__ == "__main__":
    hub = register_device()
    if hub:
        with open("assigned_hub.txt", "w") as f:
            f.write(hub)
        print(f"\nHub guardado en assigned_hub.txt: {hub}")