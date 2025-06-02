import os
import requests
from flask import Flask, request
from dotenv import load_dotenv
from groq import Groq # Importer le client Groq

# Charger les variables d'environnement depuis le fichier .env
# Ceci est fait au début pour s'assurer que les clés sont disponibles
load_dotenv()

app = Flask(__name__)

# Récupérer les variables d'environnement
# Elles seront lues depuis .env en local ou depuis les configs de Render en production
PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Vérifier que les variables essentielles sont chargées
# C'est une bonne pratique pour éviter que l'application ne démarre avec des configs manquantes
if not PAGE_ACCESS_TOKEN or not VERIFY_TOKEN or not GROQ_API_KEY:
    raise ValueError("Erreur: Les variables d'environnement (FACEBOOK_PAGE_ACCESS_TOKEN, FACEBOOK_VERIFY_TOKEN, GROQ_API_KEY) doivent être définies.")

# Initialiser le client Groq avec votre clé API
client = Groq(api_key=GROQ_API_KEY)

# --- Mémoire Conversationnelle ---
# Ce dictionnaire stocke l'historique de chaque conversation, indexé par l'ID de l'expéditeur Facebook.
# Il est en RAM, donc temporaire. Pour un usage en production intensive, une base de données serait mieux.
conversation_history = {}
# Nombre maximum de messages (utilisateur + IA) à conserver dans l'historique pour l'IA.
# 6 messages = 1 message système + 3 messages utilisateur + 2 réponses IA (ou 2 utilisateurs + 3 IA)
MAX_HISTORY_MESSAGES = 6 

@app.route('/', methods=['GET'])
def verify():
    """
    Point d'entrée GET utilisé par Facebook pour vérifier l'URL de votre webhook.
    Facebook envoie un 'hub.challenge' que vous devez renvoyer si le 'hub.verify_token' correspond.
    """
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.challenge"):
        if not request.args.get("hub.verify_token") == VERIFY_TOKEN:
            # Si le token de vérification ne correspond pas, c'est une requête non autorisée
            return "Verification token mismatch", 403
        # Si le token correspond, renvoyer le challenge de Facebook
        return request.args["hub.challenge"], 200
    # Message par défaut si quelqu'un accède à l'URL sans requête de vérification Facebook
    return "Hello world, je suis votre bot Messenger avec Groq!", 200

@app.route('/', methods=['POST'])
def webhook():
    """
    Point d'entrée POST où Facebook envoie les événements (nouveaux messages, etc.).
    """
    data = request.get_json() # Récupère les données JSON envoyées par Facebook
    log(f"Données reçues de Facebook: {data}") # Affiche les données pour le débogage dans les logs

    # S'assurer que les données proviennent bien d'une page Facebook
    if data["object"] == "page":
        for entry in data["entry"]:
            for messaging_event in entry["messaging"]:
                # Traiter seulement les messages texte non générés par le bot lui-même (pas des "échos")
                if messaging_event.get("message") and not messaging_event["message"].get("is_echo"):
                    sender_id = messaging_event["sender"]["id"] # ID de l'utilisateur qui a envoyé le message
                    message_text = messaging_event["message"]["text"] # Contenu du message texte
                    
                    log(f"Message de {sender_id}: {message_text}")

                    # --- Gestion de l'historique de conversation ---
                    if sender_id not in conversation_history:
                        # Si c'est un nouvel utilisateur ou une nouvelle session, initialiser l'historique
                        conversation_history[sender_id] = [
                            # Le message de rôle "system" donne des instructions générales à l'IA
                            {"role": "system", "content": "Tu es un assistant amical et utile pour une page Facebook. Réponds aux questions de manière concise et pertinente, en maintenant le contexte de la conversation. Sois toujours courtois et professionnel."}
                        ]
                    
                    # Ajouter le message de l'utilisateur à l'historique de conversation
                    conversation_history[sender_id].append({"role": "user", "content": message_text})
                    
                    # Tronquer l'historique si la taille dépasse MAX_HISTORY_MESSAGES
                    # On garde toujours le premier message (le message système)
                    if len(conversation_history[sender_id]) > MAX_HISTORY_MESSAGES + 1: # +1 car le message système est toujours présent
                        conversation_history[sender_id] = [conversation_history[sender_id][0]] + conversation_history[sender_id][-(MAX_HISTORY_MESSAGES):]
                        
                    log(f"Historique actuel pour {sender_id}: {conversation_history[sender_id]}")

                    # --- Génération de la réponse IA ---
                    # Appelle Groq en lui passant tout l'historique de conversation pour le contexte
                    ai_response = generate_groq_response(conversation_history[sender_id])
                    
                    # --- Envoi de la réponse à l'utilisateur ---
                    send_message(sender_id, ai_response)
                    
                    # Ajouter la réponse de l'IA à l'historique pour les futurs échanges
                    conversation_history[sender_id].append({"role": "assistant", "content": ai_response})
                    
    return "ok", 200 # Toujours renvoyer "ok" à Facebook pour confirmer la bonne réception

def generate_groq_response(messages_history):
    """
    Fonction qui interagit avec l'API de Groq pour obtenir une réponse basée sur l'historique fourni.
    """
    try:
        chat_completion = client.chat.completions.create(
            messages=messages_history, # C'est le tableau de messages qui donne le contexte à l'IA
            model="llama3-8b-8192", # Modèle de Groq. Vous pouvez aussi essayer "mixtral-8x7b-32768" ou d'autres.
        )
        # Extrait le contenu du message généré par l'IA
        return chat_completion.choices[0].message.content
    except Exception as e:
        log(f"Erreur lors de la génération de la réponse Groq : {e}")
        # Message d'erreur de fallback si Groq ne répond pas ou s'il y a un problème
        return "Désolé, je rencontre un problème technique pour le moment. Veuillez réessayer plus tard."

def send_message(recipient_id, message_text):
    """
    Fonction qui envoie un message texte à un utilisateur via l'API Messenger de Facebook.
    """
    params = {
        "access_token": PAGE_ACCESS_TOKEN # Jeton d'accès de votre page Facebook
    }
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "recipient": {"id": recipient_id}, # ID de l'utilisateur à qui envoyer le message
        "message": {"text": message_text} # Le texte du message à envoyer
    }
    # URL de l'API Messenger de Facebook (version actuelle à la date de l'écriture)
    r = requests.post("https://graph.facebook.com/v19.0/me/messages", params=params, headers=headers, json=data)
    if r.status_code != 200:
        log(f"Erreur d'envoi de message : {r.status_code} - {r.text}")
    else:
        log(f"Message envoyé avec succès à {recipient_id}: {message_text[:50]}...") # Log des 50 premiers caractères envoyés

def log(msg):
    """
    Fonction utilitaire simple pour afficher des messages dans la console.
    Ces messages seront visibles dans les logs de votre service sur Render.
    """
    print(str(msg))

if __name__ == '__main__':
    # Lance l'application Flask. En développement local, elle écoute sur le port 5000.
    # En production sur Render, Render lui assignera un port via la variable d'environnement $PORT.
    app.run(debug=True) # debug=True est utile pour le développement, à désactiver en production si non nécessaire.