"""
Bloque 1: Generar un SAS Token para Azure IoT.
No hace ninguna petición HTTP todavia. Solo construye el token y lo imprime.
"""

import base64
import hashlib
import hmac
import time
import urllib.parse
import os

# --- Cargar credenciales desde iotc.env ---
def load_env(path="iotc.env"):
    env = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

# --- Generar SAS Token ---
def build_sas_token(resource_uri: str, primary_key_b64: str, ttl_seconds: int = 3600) -> str:
    """
    resource_uri:    el recurso a firmar, SIN url-encode.
                     Ej: '0ne00FE096E/registrations/b7oz89ybow'
    primary_key_b64: clave simetrica del dispositivo (base64)
    ttl_seconds:     tiempo de vida del token en segundos
    """
    # 1. Expiracion (unix timestamp)
    expiry = int(time.time()) + ttl_seconds

    # 2. Recurso URL-encoded (una sola vez, para el parametro sr=)
    uri_encoded = urllib.parse.quote(resource_uri, safe="")

    # 3. String a firmar: "<uri_encoded>\n<expiry>"
    string_to_sign = f"{uri_encoded}\n{expiry}"

    # 4. Decodificar la key (viene en base64)
    key_bytes = base64.b64decode(primary_key_b64)

    # 5. HMAC-SHA256 y luego base64
    signature = hmac.new(key_bytes, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    signature_b64 = base64.b64encode(signature).decode("utf-8")

    # 6. URL-encode de la firma (porque va como parametro)
    signature_encoded = urllib.parse.quote(signature_b64, safe="")

    # 7. Armar el string final
    return f"SharedAccessSignature sr={uri_encoded}&sig={signature_encoded}&se={expiry}"


# --- Prueba ---
if __name__ == "__main__":
    env = load_env()
    id_scope = env["ID_SCOPE"]
    device_id = env["DEVICE_ID"]
    key = env["PRIMARY_KEY"]

    # Recurso para DPS:
    resource = f"{id_scope}/registrations/{device_id}"

    token = build_sas_token(resource, key, ttl_seconds=3600)

    print("Recurso firmado:", resource)
    print("Token generado:")
    print(token)
    print()
    print("Longitud del token:", len(token), "caracteres")