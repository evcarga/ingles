import os
import json
import random
import time
import wave
import re
import io
from google import genai
from google.genai import types
from supabase import create_client, Client


import sys
import io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
# === 1. CONFIGURACIÓN ===

# ⚠️ CONFIGURACIÓN DE SUPABASE (Variables de Entorno en Render)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") # Usa Service Role Key si es posible

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET_NAME = "audios"

# ⚠️ COLOCA TUS API KEYS DE GEMINI
API_KEYS = [
    "AIzaSyBF7FepqeN1lZ2FwKQfi6458t5r-mA3pn0",
    "AIzaSyBwPS8g-536ZbZH36kPTl_pgyL8zNFppds",
    "AIzaSyAOYpLBat-nhb9bCpv1j_AtqTUgreu3Pk8",
    "AIzaSyCd0cQlSfi-dZ861h9alMfFDJrDmXJK8bM",
    "AIzaSyAKsRWw5_mXwwcBUhXbA9y9HkgrdrXKTaM",
    "AIzaSyA1eHiNHRBRBU1y3Kt9DwBhN9i-LYazKCQ"
]

# ⚠️ CONFIGURACIÓN DEL PROCESO
NIVEL_OBJETIVO = "A1"
GRUPO_INICIO = 1
GRUPO_FIN = 2

ARCHIVO_JSON = "palabras_sin_repetir_final.json"
CARPETA_SALIDA_LOCAL = "temp_audios" # Carpeta temporal local (Render la borra, pero la usamos para subir)

# --- VOCES DISPONIBLES ---
GEMINI_VOICES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat"
]

current_key_index = 0

# === 2. FUNCIONES DE SUPABASE ===

def check_word_exists_in_db(palabra):
    """Verifica si la palabra ya fue marcada como 'generado' en la base de datos."""
    try:
        response = supabase.table("audios").select("*").eq("palabra", palabra).eq("proceso", "generado").execute()
        return len(response.data) > 0
    except Exception as e:
        print(f"⚠️ Error consultando DB para '{palabra}': {e}")
        return False

def upsert_word_status(palabra, status):
    """Inserta o actualiza el estado de la palabra en la base de datos."""
    try:
        data = {"palabra": palabra, "proceso": status}
        # Usamos upsert para insertar si no existe o actualizar si ya falló anteriormente
        supabase.table("audios").upsert(data).execute()
    except Exception as e:
        print(f"❌ Error guardando en DB '{palabra}': {e}")

def upload_to_supabase_storage(file_path, storage_path):
    """Sube el archivo local al bucket de Supabase."""
    try:
        with open(file_path, 'rb') as f:
            # Intentamos subir. Si ya existe, supabase puede dar error o sobrescribir dependiendo la config.
            # Para asegurar que se suba, podemos eliminar primero o simplemente subir.
            # Aquí intentamos subir directo.
            supabase.storage.from_(BUCKET_NAME).upload(
                path=storage_path,
                file=f,
                file_options={"content-type": "audio/wav"}
            )
        print(f"☁️  Subido a Storage: {storage_path}")
    except Exception as e:
        # Si el error es que ya existe, a veces es aceptable, pero imprimimos el error
        print(f"⚠️ Error subiendo a Storage ({storage_path}): {e}")
        # Opcional: Intentar borrar y subir de nuevo si es necesario
        try:
            supabase.storage.from_(BUCKET_NAME).remove([storage_path])
            with open(file_path, 'rb') as f:
                supabase.storage.from_(BUCKET_NAME).upload(storage_path, f, {"content-type": "audio/wav"})
        except:
            pass

# === 3. FUNCIONES DE UTILIDAD ===

def cargar_json(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"❌ Error: No se encontró el archivo {filename}")
        return {}

def get_wave_bytes(pcm, channels=1, rate=24000, sample_width=2):
    """Genera los bytes del archivo WAV en memoria para no depender tanto de disco."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    buf.seek(0)
    return buf.read()

def save_local_wav(filename, pcm):
    """Guarda el archivo WAV localmente para luego subirlo."""
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

# === 4. LÓGICA DE GENERACIÓN (KEY + VOZ) ===

def get_current_client():
    global current_key_index
    key = API_KEYS[current_key_index]
    return genai.Client(api_key=key)

def rotar_key():
    global current_key_index
    current_key_index = (current_key_index + 1) % len(API_KEYS)
    print(f"   🔄 Rotando Key -> Índice {current_key_index}")

def generate_audio_con_fallback(text, local_folder_path):
    """
    Retorna:
    0: Si ya existía en DB (no hizo nada)
    1: Si se generó con éxito
    2: Si falló totalmente
    """
    
    # 1. Verificar en Base de Datos primero (Crucial para Render Cron)
    if check_word_exists_in_db(text):
        print(f"⏭️  Ya existe en DB: '{text}' (Saltando)")
        return 0

    # Configuración de rutas
    filename = f"{sanitize_filename(text)}.wav"
    local_filepath = os.path.join(local_folder_path, filename)
    
    # Estructura en Storage: Nivel/Grupo/palabra.wav
    # local_folder_path es algo como "temp_audios/A1/G1", extraemos la parte relativa
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
                    
                    # Guardar localmente
                    save_local_wav(local_filepath, data)
                    
                    # Subir a Supabase Storage
                    upload_to_supabase_storage(local_filepath, storage_path)
                    
                    # Guardar en Supabase DB
                    upsert_word_status(text, "generado")
                    
                    print(f"✅ Éxito ({voz_actual})")
                    return 1 # Código 1: Se generó nuevo
                else:
                    raise Exception("Respuesta vacía API")

            except Exception as e:
                rotar_key()
                intentos_keys += 1
                time.sleep(1) # Breve pausa al cambiar de llave
        
        print(f"\n   ⚠️ Falló voz {voz_actual}, probando siguiente...", end="")
        time.sleep(1)

    print(f"\n❌ ERROR FINAL: No se pudo generar '{text}'.")
    # Guardar fallo en DB
    upsert_word_status(text, "fallo")
    return 2 # Código 2: Fallo total

# === 5. BLOQUE PRINCIPAL ===

def main():
    datos = cargar_json(ARCHIVO_JSON)
    if not datos: return

    if NIVEL_OBJETIVO not in datos:
        print(f"❌ El nivel '{NIVEL_OBJETIVO}' no existe en el JSON.")
        return

    print(f"🚀 INICIANDO TTS | Nivel: {NIVEL_OBJETIVO} | Rango: G{GRUPO_INICIO} - G{GRUPO_FIN}")
    
    lista_grupos = datos[NIVEL_OBJETIVO]

    for objeto_grupo in lista_grupos:
        for nombre_grupo, lista_palabras in objeto_grupo.items():
            
            numero_grupo = extraer_numero_grupo(nombre_grupo)
            
            if numero_grupo != -1 and GRUPO_INICIO <= numero_grupo <= GRUPO_FIN:
                
                ruta_carpeta = os.path.join(CARPETA_SALIDA_LOCAL, NIVEL_OBJETIVO, nombre_grupo)
                print(f"\n📂 === GRUPO {nombre_grupo} ({len(lista_palabras)} palabras) ===")
                
                for palabra in lista_palabras:
                    
                    # status: 0=Existe en DB, 1=Generado, 2=Error
                    status = generate_audio_con_fallback(palabra, ruta_carpeta)
                    
                    if status == 1:
                        # Éxito: esperamos 10 segundos por la solicitud a la API
                        time.sleep(10)
                    elif status == 2:
                        # Si hubo error (se hicieron intentos de solicitud): esperamos 10 segundos
                        time.sleep(10)
                    
                    # Si status == 0 (Ya existe en DB), NO hubo solicitud a la API, 
                    # por lo tanto NO esperamos y pasamos a la siguiente palabra inmediatamente.

    print("\n" + "="*40)
    print(f"✅ Proceso finalizado.")

if __name__ == "__main__":
    main()