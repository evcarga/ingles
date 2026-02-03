import os
import json
import random
import time
import wave
import re
import io
import sys
import threading
from flask import Flask
from google import genai
from google.genai import types
from supabase import create_client, Client

# === 1. CONFIGURACIÓN WEB ===
app = Flask(__name__)
is_processing = False

# === 2. CONFIGURACIÓN SUPABASE ===
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET_NAME = "audios"

# === 3. CONFIGURACIÓN GEMINI ===
# Leemos las claves desde una variable de entorno separada por comas
raw_keys = os.environ.get("GEMINI_API_KEYS", "")

if not raw_keys:
    print("❌ ERROR CRÍTICO: La variable de entorno 'GEMINI_API_KEYS' no está definida.")
    # Dejamos la lista vacía, el programa fallará al intentar generar, pero arrancará.
    API_KEYS = []
else:
    # Separamos por comas, eliminamos espacios y filtramos strings vacíos
    API_KEYS = [key.strip() for key in raw_keys.split(",") if key.strip()]

# Imprimimos cuántas keys cargamos para verificar en los Logs
print(f"🔑 Se cargaron {len(API_KEYS)} claves de API de Google.")

# === 4. CONFIGURACIÓN PROCESO ===
NIVEL_OBJETIVO = "A1"
GRUPO_INICIO = 1
GRUPO_FIN = 2
ARCHIVO_JSON = "palabras_sin_repetir_final.json"
CARPETA_SALIDA_LOCAL = "temp_audios"

GEMINI_VOICES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat"
]

current_key_index = 0

# --- FIX PARA WINDOWS (Solo se ejecuta si estás en local, no afecta a Render) ---
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# === 5. FUNCIONES LÓGICAS (IGUAL QUE ANTES) ===

def check_word_exists_in_db(palabra):
    try:
        response = supabase.table("audios").select("*").eq("palabra", palabra).eq("proceso", "generado").execute()
        return len(response.data) > 0
    except Exception as e:
        print(f"⚠️ Error consultando DB: {e}")
        return False

def upsert_word_status(palabra, status):
    try:
        data = {"palabra": palabra, "proceso": status}
        supabase.table("audios").upsert(data).execute()
    except Exception as e:
        print(f"❌ Error guardando en DB: {e}")

def upload_to_supabase_storage(file_path, storage_path):
    try:
        with open(file_path, 'rb') as f:
            supabase.storage.from_(BUCKET_NAME).upload(
                path=storage_path,
                file=f,
                file_options={"content-type": "audio/wav"}
            )
        print(f"☁️  Subido a Storage: {storage_path}")
    except Exception as e:
        # Si el error es "duplicate", es normal que exista, lo ignoramos
        print(f"⚠️ Info Storage ({storage_path}): {e}")
        # Intento de sobrescritura si es necesario (opcional)
        try:
            supabase.storage.from_(BUCKET_NAME).remove([storage_path])
            with open(file_path, 'rb') as f:
                supabase.storage.from_(BUCKET_NAME).upload(storage_path, f, {"content-type": "audio/wav"})
        except:
            pass

def cargar_json(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"❌ Error: No se encontró el archivo {filename}")
        return {}

def save_local_wav(filename, pcm):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "wb") as wf:
        wf.write(pcm)

def sanitize_filename(text):
    safe_name = text.replace("/", "_").replace("\\", "_").strip()
    return "".join([c for c in safe_name if c.isalnum() or c in (' ', '-', '_')]).strip()

def extraer_numero_grupo(nombre_grupo):
    match = re.search(r'\d+', nombre_grupo)
    if match:
        return int(match.group())
    return -1

def get_current_client():
    global current_key_index
    key = API_KEYS[current_key_index]
    return genai.Client(api_key=key)

def rotar_key():
    global current_key_index
    current_key_index = (current_key_index + 1) % len(API_KEYS)
    print(f"   🔄 Rotando Key -> Índice {current_key_index}")

def generate_audio_con_fallback(text, local_folder_path):
    if check_word_exists_in_db(text):
        print(f"⏭️  Ya existe en DB: '{text}' (Saltando)")
        return 0

    filename = f"{sanitize_filename(text)}.wav"
    local_filepath = os.path.join(local_folder_path, filename)
    storage_path = local_folder_path.replace(CARPETA_SALIDA_LOCAL + "/", "") + "/" + filename

    voces_a_probar = list(GEMINI_VOICES)
    random.shuffle(voces_a_probar)
    voces_a_probar = voces_a_probar[:5] 

    print(f"🎙️  Generando: '{text}' ... ", end="")

    for voz_actual in voces_a_probar:
        intentos_keys = 0
        max_intentos_keys = len(API_KEYS) 

        while intentos_keys < max_intentos_keys:
            try:
                client = get_current_client()
                
                response = client.models.generate_content(
                    model="gemini-2.5-flash-preview-tts",
                    contents=text,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=voz_actual,
                                )
                            )
                        ),
                    )
                )

                if response.candidates and response.candidates[0].content.parts:
                    data = response.candidates[0].content.parts[0].inline_data.data
                    save_local_wav(local_filepath, data)
                    upload_to_supabase_storage(local_filepath, storage_path)
                    upsert_word_status(text, "generado")
                    print(f"✅ Éxito ({voz_actual})")
                    return 1
                else:
                    raise Exception("Respuesta vacía API")

            except Exception as e:
                rotar_key()
                intentos_keys += 1
                time.sleep(1)
        
        print(f"\n   ⚠️ Falló voz {voz_actual}, probando siguiente...", end="")
        time.sleep(1)

    print(f"\n❌ ERROR FINAL: No se pudo generar '{text}'.")
    upsert_word_status(text, "fallo")
    return 2

# === 6. FUNCIÓN PRINCIPAL DEL PROCESO ===

def ejecutar_proceso_tts():
    global is_processing
    try:
        datos = cargar_json(ARCHIVO_JSON)
        if not datos: return

        if NIVEL_OBJETIVO not in datos:
            print(f"❌ El nivel '{NIVEL_OBJETIVO}' no existe en el JSON.")
            return

        print(f"🚀 INICIANDO TTS | Nivel: {NIVEL_OBJETIVO} | Rango: G{GRUPO_INICIO} - G{GRUPO_FIN}")
        
        lista_grupos = datos[NIVEL_OBJETIVO]
        procesadas_en_sesion = 0

        for objeto_grupo in lista_grupos:
            for nombre_grupo, lista_palabras in objeto_grupo.items():
                numero_grupo = extraer_numero_grupo(nombre_grupo)
                
                if numero_grupo != -1 and GRUPO_INICIO <= numero_grupo <= GRUPO_FIN:
                    ruta_carpeta = os.path.join(CARPETA_SALIDA_LOCAL, NIVEL_OBJETIVO, nombre_grupo)
                    print(f"\n📂 === GRUPO {nombre_grupo} ({len(lista_palabras)} palabras) ===")
                    
                    for palabra in lista_palabras:
                        status = generate_audio_con_fallback(palabra, ruta_carpeta)
                        
                        if status == 1:
                            procesadas_en_sesion += 1
                            time.sleep(10) # 10 segundos entre peticiones
                        elif status == 2:
                            time.sleep(10)

        print(f"\n✅ Proceso finalizado. Palabras procesadas en esta sesión: {procesadas_en_sesion}")

    except Exception as e:
        print(f"❌ Error fatal en el hilo de ejecución: {e}")
    finally:
        global is_processing
        is_processing = False
        print("🔒 Hilo de proceso liberado.")


# === 7. RUTAS DE FLASK ===

@app.route('/')
def home():
    return "Servidor TTS Activo. Usa /run para iniciar la generación."

@app.route('/run')
def run_job():
    global is_processing
    
    if is_processing:
        return "El proceso ya está corriendo. Por favor espera a que termine."
    
    is_processing = True
    # Iniciar el proceso en un hilo separado para no bloquear la respuesta HTTP
    thread = threading.Thread(target=ejecutar_proceso_tts)
    thread.start()
    
    return "✅ Proceso iniciado en segundo plano. Revisa los Logs de Render para ver el progreso."

# === 8. ARRANQUE ===
if __name__ == "__main__":
    # Render asigna un puerto dinámico, por defecto usamos 10000 si es local
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)