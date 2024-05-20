import logging
from flask import Flask, request, jsonify, current_app
import json
import requests
import re
import os
import time
import shelve
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")

client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)

# Function to log HTTP responses
def log_http_response(response):
    logging.info(f"Status: {response.status_code}")
    logging.info(f"Content-type: {response.headers.get('content-type')}")
    logging.info(f"Body: {response.text}")

# Function to create text message input for WhatsApp API
def get_text_message_input(recipient, text):
    return json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    })

# Function to send a message using WhatsApp API
def send_message(recipient, text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = get_text_message_input(recipient, text)
    response = requests.post(url, headers=headers, data=payload)
    if response.status_code != 200:
        logging.error(f"Failed to send message: {response.text}")
    log_http_response(response)
    return response

# Function to process text for WhatsApp
def process_text_for_whatsapp(text):
    pattern = r"\【.*?\】"
    text = re.sub(pattern, "", text).strip()
    pattern = r"\*\*(.*?)\*\*"
    replacement = r"*\1*"
    whatsapp_style_text = re.sub(pattern, replacement, text)
    return whatsapp_style_text

# Function to process incoming WhatsApp messages
def process_whatsapp_message(body):
    try:
        wa_id = body["entry"][0]["changes"][0]["value"]["contacts"][0]["wa_id"]
        name = body["entry"][0]["changes"][0]["value"]["contacts"][0]["profile"]["name"]
        message = body["entry"][0]["changes"][0]["value"]["messages"][0]
        message_body = message["text"]["body"]

        response_text = generate_response(message_body, wa_id, name)
        response_text = process_text_for_whatsapp(response_text)
        send_message(wa_id, response_text)

    except Exception as e:
        logging.error(f"Error processing WhatsApp message: {e}")

# Flask route to handle incoming webhook POST requests
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if is_valid_whatsapp_message(data):
        process_whatsapp_message(data)
    return jsonify({'status': 'success'}), 200

# Function to validate the structure of the incoming WhatsApp message
def is_valid_whatsapp_message(body):
    return (
        body.get("object")
        and body.get("entry")
        and body["entry"][0].get("changes")
        and body["entry"][0]["changes"][0].get("value")
        and body["entry"][0]["changes"][0]["value"].get("messages")
        and body["entry"][0]["changes"][0]["value"]["messages"][0]
    )

# Function to check if a thread exists for a given wa_id
def check_if_thread_exists(wa_id):
    with shelve.open("threads_db") as threads_shelf:
        return threads_shelf.get(wa_id, None)

# Function to store a thread ID for a given wa_id
def store_thread(wa_id, thread_id):
    with shelve.open("threads_db", writeback=True) as threads_shelf:
        threads_shelf[wa_id] = thread_id

# Function to run the OpenAI assistant
def run_assistant(thread, name):
    assistant = client.beta.assistants.retrieve(OPENAI_ASSISTANT_ID)
    run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=assistant.id)
    while run.status != "completed":
        time.sleep(0.5)
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
    messages = client.beta.threads.messages.list(thread_id=thread.id)
    new_message = messages.data[0].content[0].text.value
    logging.info(f"Generated message: {new_message}")
    return new_message

# Function to generate a response using the OpenAI assistant
def generate_response(message_body, wa_id, name):
    thread_id = check_if_thread_exists(wa_id)
    if thread_id is None:
        logging.info(f"Creating new thread for {name} with wa_id {wa_id}")
        thread = client.beta.threads.create()
        store_thread(wa_id, thread.id)
        thread_id = thread.id
    else:
        logging.info(f"Retrieving existing thread for {name} with wa_id {wa_id}")
        thread = client.beta.threads.retrieve(thread_id)
    message = client.beta.threads.messages.create(thread_id=thread_id, role="user", content=message_body)
    new_message = run_assistant(thread, name)
    return new_message
