# personalized_ad_service.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import os
import sys
import logging
from dotenv import load_dotenv
from supabase import create_client, Client
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from openai import OpenAI
import json
import urllib.parse
import time
from datetime import datetime, date, timedelta, timezone
import requests

# Load environment variables for local development
load_dotenv()

# --- Logging Setup ---
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI()

# --- SUPABASE CONFIGURATION ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_TABLE_NAME = "bookings"
EMAIL_INTERACTIONS_TABLE_NAME = "email_interactions"

if not SUPABASE_URL or not SUPABASE_KEY:
    logging.error("Supabase URL or Key environment variables are not set for personalized_ad_service.")
    raise ValueError("Supabase credentials not found. Please set SUPABASE_URL and SUPABASE_KEY.")
    
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- EMAIL CONFIGURATION ---
EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT_STR = os.getenv("EMAIL_PORT")
EMAIL_PORT = int(EMAIL_PORT_STR) if EMAIL_PORT_STR else 0
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

ENABLE_SMTP_SENDING = all([EMAIL_HOST, EMAIL_PORT, EMAIL_ADDRESS, EMAIL_PASSWORD])
if not ENABLE_SMTP_SENDING:
    logging.error("SMTP credentials not fully configured for personalized_ad_service. Email sending will be disabled.")
else:
    logging.info("SMTP sending enabled for personalized_ad_service.")

# --- API KEY CONFIGURATION ---
# The code was using OPENAI_API_KEY for the Gemini API call.
# This variable should be separate for clarity and to prevent issues.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

openai_client = None
if OPENAI_API_KEY:
    try:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        logging.error(f"Failed to initialize OpenAI client: {e}", exc_info=True)
        openai_client = None
else:
    logging.warning("OPENAI_API_KEY environment variable is not set. AI functionalities will be limited.")

# --- GOOGLE CLOUD STORAGE IMAGE URLs ---
AOE_VEHICLE_IMAGES = {
  "AOE Apex": [
    "https://storage.googleapis.com/aoe-motors-images/AOE%20Apex.jpg",
    "https://storage.googleapis.com/aoe-motors-images/AOE%20Apex_back.png",
    "https://storage.googleapis.com/aoe-motors-images/AOE%20Apex_Interior.png"
  ],
  "AOE Thunder": [
    "https://storage.googleapis.com/aoe-motors-images/AOE%20Thunder.jpg",
    "https://storage.googleapis.com/aoe-motors-images/AOE%20Thunder_Back.png",
    "https://storage.googleapis.com/aoe-motors-images/AOE%20Thunder_Interior.png"
  ],
  "AOE Volt": [
    "https://storage.googleapis.com/aoe-motors-images/AOE%20Volt.jpg",
    "https://storage.googleapis.com/aoe-motors-images/AOE%20Volt_Back.png",
    "https://storage.googleapis.com/aoe-motors-images/AOE%20Volt_Interior.png"
  ]
}
# --- VEHICLE DATA ---
AOE_VEHICLE_DATA = {
  "AOE Apex": {
    "type": "Luxury Sedan",
    "features": [
      "Premium leather interior",
      "Advanced driver-assistance systems (ADAS)",
      "Panoramic sunroof"
    ]
  },
  "AOE Volt": {
    "type": "Electric Compact",
    "features": [
      "Long-range battery (500 miles)",
      "Fast charging (80% in 20 min)",
      "Regenerative braking"
    ]
  },
  "AOE Thunder": {
    "type": "Performance SUV",
    "features": [
      "V8 Twin-Turbo Engine",
      "Adjustable air suspension",
      "High-performance braking system"
    ]
  }
}

# --- Custom Messages based on vehicle type ---
AD_MESSAGES = {
  "Luxury Sedan": "Experience sophistication. Discover the new level of luxury.",
  "Electric Compact": "Drive the future. Electrify your journey with groundbreaking technology.",
  "Performance SUV": "Unleash power. Command the road with unparalleled performance."
}

# --- API HELPER FUNCTIONS ---
def send_email_via_smtp(recipient_email, subject, body_html):
    """Sends an HTML email using SMTP_SSL."""
    if not ENABLE_SMTP_SENDING:
        logging.error("SMTP sending is disabled due to missing credentials.")
        return False
    
    msg = MIMEMultipart()
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html"))

    try:
        if EMAIL_PORT == 465:
            server = smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT)
        elif EMAIL_PORT == 587:
            server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT)
            server.starttls()
        else:
            logging.error(f"Unsupported SMTP port: {EMAIL_PORT}. Email sending failed for {recipient_email}.")
            return False

        with server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
        
        logging.info(f"Email successfully sent via SMTP to {recipient_email}!")
        return True
    except Exception as e:
        logging.error(f"Failed to send email via SMTP to {recipient_email}: {e}", exc_info=True)
        return False

def log_email_interaction(request_id, event_type):
    """Logs an email interaction to the email_interactions table."""
    try:
        data = {
            "request_id": request_id,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        supabase.from_(EMAIL_INTERACTIONS_TABLE_NAME).insert(data).execute()
        logging.info(f"Logged email interaction: {event_type} for {request_id}.")
    except Exception as e:
        logging.error(f"Error logging email interaction for {request_id}: {e}", exc_info=True)

def generate_audio(name, vehicle):
    """Generates an audio clip from text using the Gemini TTS API."""
    vehicle_type = AOE_VEHICLE_DATA.get(vehicle, {}).get('type', 'vehicle')
    message = AD_MESSAGES.get(vehicle_type, "your perfect vehicle.")
    text_prompt = f"Say cheerfully: Hello {name}, we saw you were interested in the {vehicle}. {message}. Our team is ready for you to take a test drive. Please call us at (800) 555-0199 or reply to this email to schedule a new appointment."

    payload = {
      "contents": [{"parts": [{"text": text_prompt}]}],
      "generationConfig": {
        "responseModalities": ["AUDIO"],
        "speechConfig": {
          "voiceConfig": {
            "prebuiltVoiceConfig": {"voiceName": "Kore"}
          }
        }
      }
    }
    api_key = GEMINI_API_KEY or "" # Use the new GEMINI_API_KEY variable
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={api_key}"
    
    # Simple retry logic with exponential backoff
    for i in range(3):
        try:
            # Fix: Increase the timeout to 30 seconds
            response = requests.post(api_url, json=payload, headers={'Content-Type': 'application/json'}, timeout=30)
            response.raise_for_status()
            result = response.json()
            part = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0]
            audio_data = part.get('inlineData', {}).get('data')
            if not audio_data:
                raise ValueError("No audio data received from API.")
            return audio_data
        except requests.exceptions.RequestException as e:
            logging.warning(f"Attempt {i+1} failed to generate audio: {e}")
            time.sleep(2 ** i) # Exponential backoff
        except Exception as e:
            logging.error(f"Error generating audio: {e}")
            return None
    logging.error("Failed to generate audio after multiple retries.")
    return None

def generate_landing_page_html(lead_data, audio_data_base64):
    """Generates the full HTML for the ad landing page."""
    vehicle = lead_data.get('vehicle', 'vehicle')
    full_name = lead_data.get('full_name', 'Customer')
    
    vehicle_data = AOE_VEHICLE_DATA.get(vehicle, {})
    vehicle_images = AOE_VEHICLE_IMAGES.get(vehicle, [])
    vehicle_features = vehicle_data.get('features', [])
    vehicle_type = vehicle_data.get('type', '')
    ad_message = AD_MESSAGES.get(vehicle_type, f"your perfect {vehicle_type}.")
    
    # Convert audio data to a data URL
    audio_data_url = f"data:audio/wav;base64,{audio_data_base64}" if audio_data_base64 else ""

    # Generate image HTML for the grid
    images_html = ""
    for image_src in vehicle_images:
        images_html += f"""
          <div class="rounded-2xl overflow-hidden shadow-lg border border-gray-700">
            <img src="{image_src}" alt="Image of {vehicle}" class="w-full h-auto object-cover" onerror="this.onerror=null; this.src='https://placehold.co/400x225/1F2937/D1D5DB?text=Image+Failed+to+Load';">
          </div>
        """
    
    # Generate features list HTML
    features_html = ""
    for feature in vehicle_features:
        features_html += f"""
          <li class="flex items-start">
            <span class="text-blue-400 mr-2">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 mt-1" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" />
                </svg>
            </span>
            <span>{feature}</span>
          </li>
        """

    # Full HTML template
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Your Personalized Ad</title>
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="min-h-screen bg-gray-900 text-white flex flex-col items-center justify-center p-4 font-sans">
      <div class="w-full max-w-4xl bg-gray-800 p-8 rounded-2xl shadow-xl border border-gray-700">
        <p class="text-center text-gray-400 mb-8">
          A special message for you from the AOE Motors team!
        </p>
        
        <div class="mt-8">
          <h2 class="text-2xl sm:text-3xl font-bold text-white text-center mb-6 animate-fade-in">
            Hello {full_name}, {ad_message}
          </h2>
          
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
            {images_html}
          </div>

          <div class="p-6 bg-gray-700 rounded-2xl shadow-inner border border-gray-600">
            <div class="flex items-center justify-between mb-4">
              <h3 class="text-xl font-semibold">Key Features</h3>
              <button
                onclick="document.getElementById('audio-player').play();"
                class="flex items-center gap-2 px-4 py-2 bg-teal-500 hover:bg-teal-600 text-white font-semibold rounded-full shadow-md transition-colors duration-300 transform hover:scale-105"
                aria-label="Play Personalized Ad Audio"
              >
                <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z" />
                </svg>
                Play Audio
              </button>
            </div>
            <ul class="text-gray-300 text-sm list-disc list-inside space-y-2 mt-4">
              {features_html}
            </ul>
          </div>
          <audio id="audio-player" src="{audio_data_url}" preload="auto"></audio>
        </div>
      </div>
    </body>
    </html>
    """

# --- FASTAPI ENDPOINTS ---
class AdEmailRequest(BaseModel):
    request_id: str

@app.post("/send-ad-email")
async def send_ad_email(request_body: AdEmailRequest):
    """
    Endpoint to send a personalized ad email to a customer.
    This endpoint is triggered by the dashboard.
    """
    request_id = request_body.request_id
    logging.info(f"Received request to send personalized ad email for lead {request_id}.")

    try:
        # 1. Fetch lead data from Supabase
        response = supabase.from_(SUPABASE_TABLE_NAME).select(
            "email, full_name, vehicle"
        ).eq('request_id', request_id).single().execute()
        
        if not response.data:
            logging.error(f"Lead {request_id} not found in Supabase.")
            raise HTTPException(status_code=404, detail="Lead not found.")

        lead_data = response.data
        customer_email = lead_data['email']
        customer_name = lead_data['full_name']
        vehicle = lead_data['vehicle']
        
        # 2. Get image for the email (first image from the set)
        email_image_url = AOE_VEHICLE_IMAGES.get(vehicle, ["https://placehold.co/600x338/1F2937/D1D5DB?text=AOE+Motors"])[0]

        # 3. Build the URL for the landing page
        ad_page_url = f"https://aoe-personalized-ad.onrender.com/ad?id={request_id}" # <-- IMPORTANT: Replace with your deployed URL

        # 4. Construct the email body
        email_body_html = f"""
        <!DOCTYPE html>
        <html>
        <body>
          <p>Hello {customer_name},</p>
          <p>We saw you were interested in the {vehicle}. Our team has a personalized message for you.</p>
          <p>Take a look at the stunning {vehicle}:</p>
          <img src="{email_image_url}" alt="Image of {vehicle}" style="max-width: 100%; height: auto; border-radius: 8px;">
          <p>To view your personal message, click the button below:</p>
          <a href="{ad_page_url}" style="display:inline-block; padding:10px 20px; color:#ffffff; background-color:#14b8a6; text-decoration:none; border-radius:8px;">Listen to Your Ad</a>
          <p>Sincerely,</p>
          <p>Your AOE Motors Team</p>
        </body>
        </html>
        """
        email_subject = f"A special message for you about the {vehicle}!"

        # 5. Send the email
        email_sent = send_email_via_smtp(customer_email, email_subject, email_body_html)
        if email_sent:
            # 6. Update action status and log
            supabase.from_(SUPABASE_TABLE_NAME).update(
                {'action_status': 'Personalized Ad Sent'}
            ).eq('request_id', request_id).execute()
            log_email_interaction(request_id, 'personalized_ad_email_sent')
            return {"status": "success", "message": "Personalized ad email sent successfully."}
        else:
            raise HTTPException(status_code=500, detail="Failed to send personalized ad email.")

    except Exception as e:
        logging.error(f"🚨 An unexpected error occurred during personalized ad email processing for {request_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")

@app.get("/ad", response_class=HTMLResponse)
async def ad_landing_page(id: str):
    """
    Endpoint to serve the personalized ad landing page.
    This page is rendered on the fly with lead-specific data.
    """
    if not id:
        return HTMLResponse("<h1>Error: Missing lead ID.</h1>", status_code=400)
    
    try:
        # 1. Fetch lead data from Supabase
        response = supabase.from_(SUPABASE_TABLE_NAME).select(
            "full_name, vehicle"
        ).eq('request_id', id).single().execute()
        
        if not response.data:
            return HTMLResponse("<h1>Error: Lead not found.</h1>", status_code=404)
        
        lead_data = response.data

        # 2. Generate personalized audio data
        audio_data_base64 = generate_audio(lead_data['full_name'], lead_data['vehicle'])
        
        # 3. Generate the full HTML for the landing page
        html_content = generate_landing_page_html(lead_data, audio_data_base64)
        
        return HTMLResponse(content=html_content, status_code=200)

    except Exception as e:
        logging.error(f"🚨 An unexpected error occurred while generating ad landing page for ID {id}: {e}", exc_info=True)
        return HTMLResponse("<h1>Internal Server Error</h1><p>Failed to generate the personalized ad. Please try again later.</p>", status_code=500)
