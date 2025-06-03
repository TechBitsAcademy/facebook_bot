import os
import requests
from flask import Flask, request
from dotenv import load_dotenv
from groq import Groq

# Charger les variables d'environnement (local uniquement)
load_dotenv()

app = Flask(__name__)

# Récupérer les variables d'environnement (Render ou local)
PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not PAGE_ACCESS_TOKEN or not VERIFY_TOKEN or not GROQ_API_KEY:
    raise ValueError("Erreur: Les variables FACEBOOK_PAGE_ACCESS_TOKEN, FACEBOOK_VERIFY_TOKEN, GROQ_API_KEY doivent être définies.")

client = Groq(api_key=GROQ_API_KEY)

conversation_history = {}
MAX_HISTORY_MESSAGES = 6

@app.route('/', methods=['GET'])
def verify():
    # Vérification Facebook webhook
    mode = request.args.get("hub.mode")
    challenge = request.args.get("hub.challenge")
    token = request.args.get("hub.verify_token")
    
    if mode == "subscribe" and challenge:
        if token == VERIFY_TOKEN:
            return challenge, 200
        else:
            return "Verification token mismatch", 403
    return "Hello world, je suis votre bot Messenger avec Groq!", 200

@app.route('/', methods=['POST'])
def webhook():
    data = request.get_json()
    log(f"Données reçues de Facebook: {data}")

    if data.get("object") == "page":
        for entry in data.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                if messaging_event.get("message") and not messaging_event["message"].get("is_echo"):
                    sender_id = messaging_event["sender"]["id"]
                    message_text = messaging_event["message"].get("text", "")
                    log(f"Message de {sender_id}: {message_text}")

                    if sender_id not in conversation_history:
                        conversation_history[sender_id] = [
                            {
                                "role": "system",
                                "content": ("Tu es un assistant amical et utile pour une page Facebook. "
                                            "Réponds aux questions de manière concise et pertinente, "
                                            "en maintenant le contexte de la conversation. "
                                            "Sois toujours courtois et professionnel.")
                            }
                        ]

                    conversation_history[sender_id].append({"role": "user", "content": message_text})

                    # Limiter l'historique
                    if len(conversation_history[sender_id]) > MAX_HISTORY_MESSAGES + 1:
                        conversation_history[sender_id] = [conversation_history[sender_id][0]] + conversation_history[sender_id][-(MAX_HISTORY_MESSAGES):]

                    log(f"Historique pour {sender_id}: {conversation_history[sender_id]}")

                    ai_response = generate_groq_response(conversation_history[sender_id])
                    send_message(sender_id, ai_response)
                    conversation_history[sender_id].append({"role": "assistant", "content": ai_response})

    return "ok", 200

def generate_groq_response(messages_history):
    try:
        completion = client.chat.completions.create(
            messages=messages_history,
            model="llama3-8b-8192",
        )
        return completion.choices[0].message.content
    except Exception as e:
        log(f"Erreur lors de la génération de la réponse Groq : {e}")
        return "Désolé, je rencontre un problème technique pour le moment. Veuillez réessayer plus tard."

def send_message(recipient_id, message_text):
    params = {"access_token": PAGE_ACCESS_TOKEN}
    headers = {"Content-Type": "application/json"}
    data = {
        "recipient": {"id": recipient_id},
        "message": {"text": message_text}
    }
    r = requests.post("https://graph.facebook.com/v19.0/me/messages", params=params, headers=headers, json=data)
    if r.status_code != 200:
        log(f"Erreur en envoyant message : {r.status_code} - {r.text}")
    else:
        log(f"Message envoyé à {recipient_id}: {message_text[:50]}...")

def log(msg):
    print(msg)

if __name__ == '__main__':
    # Pour tests locaux uniquement, Render utilise gunicorn qui ignore ce bloc
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
