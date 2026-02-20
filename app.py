import os
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Inicializar la app Flask indicando que los archivos estáticos están en la carpeta 'static'
app = Flask(__name__, static_folder='static')
CORS(app)

# OBTENER LA API KEY DE LAS VARIABLES DE ENTORNO DE RENDER
# (Esto evita que la clave sea visible en el código público)
API_KEY = os.environ.get("GEMINI_API_KEYS")

# URIs de los archivos ya subidos a Google
URI_SALUD = "https://generativelanguage.googleapis.com/v1beta/files/ikkurp1hiuei"
URI_CORRUPCION = "https://generativelanguage.googleapis.com/v1beta/files/rkv51zo5ix9c"
URI_SEGURIDAD = "https://generativelanguage.googleapis.com/v1beta/files/6wcib2234ll7"

@app.route('/')
def index():
    # Al entrar a la web, servimos el index.html
    return send_from_directory('static', 'index.html')

@app.route('/api/chat', methods=['POST'])
def chat():
    if not API_KEY:
        return jsonify({"error": "Error de configuración: API KEY no encontrada en el servidor."}), 500

    data = request.json
    user_message = data.get('message', '')
    history = data.get('history', [])

    # Configuración de los archivos para el contexto
    files_context = [
        {"fileData": {"mimeType": "application/pdf", "fileUri": URI_SALUD}},
        {"fileData": {"mimeType": "application/pdf", "fileUri": URI_CORRUPCION}},
        {"fileData": {"mimeType": "application/pdf", "fileUri": URI_SEGURIDAD}}
    ]

    # Instrucción del sistema para respuestas cortas y estilo Fajardo
    system_instruction = {
        "parts": [{
            "text": """Eres el asistente virtual oficial de la campaña de Sergio Fajardo (Dignidad y Compromiso). 
            TU OBJETIVO: Responder preguntas ciudadanas basándote ESTRICTAMENTE en los 3 documentos adjuntos (Salud, Corrupción, Seguridad).
            ESTILO DE RESPUESTA:
            1. MUY CORTO y conciso (Máximo 3 frases o viñetas).
            2. Usa un tono pedagógico, amable y sereno.
            3. Si preguntan algo que no está en los documentos, di amablemente que solo respondes sobre esos tres pilares."""
        }]
    }

    # Construir el historial para enviarlo a Gemini
    # Añadimos los archivos al último turno del usuario
    current_message = {
        "role": "user",
        "parts": files_context + [{"text": user_message}]
    }
    
    contents = history + [current_message]

    payload = {
        "system_instruction": system_instruction,
        "contents": contents
    }

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"
        response = requests.post(url, json=payload)
        response_data = response.json()

        # Verificar si Google devolvió un error
        if "error" in response_data:
            print("Error de Google:", response_data["error"])
            return jsonify({"error": "La IA no pudo procesar la solicitud."}), 500

        # Extraer la respuesta de texto
        bot_reply = response_data["candidates"][0]["content"]["parts"][0]["text"]
        return jsonify({"reply": bot_reply})

    except Exception as e:
        print(f"Error interno: {e}")
        return jsonify({"error": "Error interno del servidor."}), 500

if __name__ == '__main__':
    # Para pruebas locales
    app.run(debug=True, port=3000)