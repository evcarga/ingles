import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
# ¡IMPORTANTE! Esto permite que tu HTML (alojado en otro lado) se conecte aquí
CORS(app)

# Tu API Key debe estar en las variables de entorno de Render
API_KEY = os.environ.get("API_KEY")

# URIs de los archivos
URI_SALUD = "https://generativelanguage.googleapis.com/v1beta/files/ikkurp1hiuei"
URI_CORRUPCION = "https://generativelanguage.googleapis.com/v1beta/files/rkv51zo5ix9c"
URI_SEGURIDAD = "https://generativelanguage.googleapis.com/v1beta/files/6wcib2234ll7"

@app.route('/api/chat', methods=['POST'])
def chat():
    if not API_KEY:
        return jsonify({"error": "Falta configuración del servidor (API KEY)"}), 500

    data = request.json
    user_message = data.get('message', '')
    history = data.get('history', [])

    # Contexto de archivos
    files_context = [
        {"fileData": {"mimeType": "application/pdf", "fileUri": URI_SALUD}},
        {"fileData": {"mimeType": "application/pdf", "fileUri": URI_CORRUPCION}},
        {"fileData": {"mimeType": "application/pdf", "fileUri": URI_SEGURIDAD}}
    ]

    # Instrucción de sistema (Respuestas cortas y estilo Fajardo)
    system_instruction = {
        "parts": [{
            "text": """Eres el asistente de la campaña de Sergio Fajardo. 
            REGLAS:
            1. Responde de forma MUY CORTA y directa (máximo 2 párrafos breves).
            2. Usa tono pedagógico y decente.
            3. Basa tu respuesta SOLO en los documentos de Salud, Corrupción y Seguridad adjuntos.
            4. Si preguntan de otro tema, di amablemente que solo manejas esas 3 propuestas."""
        }]
    }

    # Armar historial + mensaje actual con archivos
    current_turn = {
        "role": "user",
        "parts": files_context + [{"text": user_message}]
    }
    
    # Solo enviamos el historial de texto previo + el turno actual con archivos
    contents = history + [current_turn]

    payload = {
        "system_instruction": system_instruction,
        "contents": contents
    }

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={API_KEY}"
        response = requests.post(url, json=payload)
        response_data = response.json()

        if "error" in response_data:
            return jsonify({"error": response_data["error"]["message"]}), 500

        bot_reply = response_data["candidates"][0]["content"]["parts"][0]["text"]
        return jsonify({"reply": bot_reply})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(port=3000)