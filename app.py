import os
import logging
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client as TwilioClient
import google.generativeai as genai
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Configuration ---
# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_CLIENT = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Google Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    GEMINI_MODEL = genai.GenerativeModel('gemini-1.5-flash') # Using flash for lower latency
else:
    print("Warning: GEMINI_API_KEY not set. LLM functionality will be disabled.")
    GEMINI_MODEL = None

# Email
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD") # App password for Gmail
RECEIVER_EMAIL = os.getenv("RECEIVER_EMAIL")
SMTP_SERVER = "smtp.gmail.com" # For Gmail
SMTP_PORT = 587 # For TLS

# Flask App
app = Flask(__name__)
app.logger.setLevel(logging.INFO) # Set logging level

# Global dictionary to store conversation context (not robust for production, but for demo)
# In production, use a database (Redis, SQL, etc.) keyed by CallSid
conversations = {}

# --- LLM Prompts ---
SYSTEM_PROMPT_RECEPTIONIST = """
You are a professional, polite, and efficient virtual receptionist named "Your Virtual Assistant" for [Your Name].
Your primary goal is to gather information from callers, summarize their requests concisely, and ensure they feel heard.
Do not attempt to resolve complex issues directly or transfer calls.
Ask clarifying questions only when necessary to get the required information (caller's name, reason for calling, contact info).
Keep your responses brief and to the point.
Start by greeting the caller and asking the purpose of their call.
"""

SYSTEM_PROMPT_SUMMARIZER = """
Summarize the following phone call conversation into a concise, actionable email for [Your Name].
Include the caller's name, their reason for calling, any contact details provided, and key actions needed.
Format it clearly for an email body.

Conversation:
"""

# --- Helper Functions ---

def send_email_summary(call_sid, summary_content):
    """
    Sends an email with the conversation summary.
    """
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECEIVER_EMAIL:
        app.logger.error("Email credentials or receiver email are not fully configured. Cannot send email.")
        return

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = f"Phone Chatbot Summary for Call {call_sid}"

    msg.attach(MIMEText(summary_content, 'plain'))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()  # Secure the connection
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        app.logger.info(f"Email summary for Call {call_sid} sent successfully to {RECEIVER_EMAIL}!")
    except Exception as e:
        app.logger.error(f"Failed to send email for Call {call_sid}: {e}")
        app.logger.error("Check sender email/password and App Password for Gmail if 2FA is on.")

# --- Flask Routes ---

@app.route("/voice", methods=['GET', 'POST'])
def voice():
    """
    Handles incoming calls from Twilio.
    Initial greeting and first prompt to the caller.
    """
    resp = VoiceResponse()
    call_sid = request.values.get('CallSid')

    # Initialize conversation for this call SID
    if call_sid not in conversations:
        conversations[call_sid] = {
            "transcript": [],
            "llm_history": [{"role": "user", "parts": SYSTEM_PROMPT_RECEPTIONIST}]
        }
        app.logger.info(f"Starting new call: {call_sid}")
        initial_greeting = "Hello, thank you for calling. Please state the purpose of your call."
        resp.say(initial_greeting)
        conversations[call_sid]["transcript"].append(f"Bot: {initial_greeting}")

    # Use <Gather> to collect speech from the caller
    resp.gather(
        input='speech',
        action=f'/gather_input?CallSid={call_sid}', # Callback URL after speech is gathered
        timeout=3, # Wait 3 seconds for speech
        speechTimeout='auto' # Automatically detect end of speech
    )
    return str(resp)

@app.route("/gather_input", methods=['POST'])
def gather_input():
    """
    Receives transcribed speech from Twilio and processes it with the LLM.
    """
    resp = VoiceResponse()
    call_sid = request.values.get('CallSid')
    speech_result = request.values.get('SpeechResult')
    call_status = request.values.get('CallStatus') # 'in-progress', 'completed', etc.

    if call_sid not in conversations:
        app.logger.warning(f"No conversation found for CallSid: {call_sid}. Ending call.")
        resp.say("I'm sorry, an error occurred. Please try again.")
        resp.hangup()
        return str(resp)

    conversation_data = conversations[call_sid]
    current_transcript = conversation_data["transcript"]
    llm_history = conversation_data["llm_history"]

    app.logger.info(f"Call {call_sid} - Received speech: {speech_result}")
    current_transcript.append(f"Caller: {speech_result}")

    if speech_result and GEMINI_MODEL:
        try:
            # Add user input to LLM history
            llm_history.append({"role": "user", "parts": speech_result})

            # Get LLM response
            chat_session = GEMINI_MODEL.start_chat(history=llm_history)
            llm_response = chat_session.send_message(speech_result).text
            app.logger.info(f"Call {call_sid} - LLM response: {llm_response}")

            # Add LLM response to history for next turn
            llm_history.append({"role": "model", "parts": llm_response})
            current_transcript.append(f"Bot: {llm_response}")

            resp.say(llm_response)
            # Continue gathering more input for multi-turn conversation
            resp.gather(
                input='speech',
                action=f'/gather_input?CallSid={call_sid}',
                timeout=3,
                speechTimeout='auto'
            )
        except Exception as e:
            app.logger.error(f"Error calling Gemini API for Call {call_sid}: {e}")
            resp.say("I'm sorry, I'm having trouble understanding right now. Please try again or hang up.")
            resp.hangup() # End call on critical error
    elif not GEMINI_MODEL:
        app.logger.warning(f"LLM not configured. Only transcribing for Call {call_sid}.")
        resp.say("Thank you. I have received your message.")
        resp.hangup()
    else:
        # No speech detected or caller hung up
        app.logger.info(f"Call {call_sid} - No speech detected or caller hung up.")
        resp.say("Thank you for calling. Goodbye.")
        resp.hangup() # End the call if no speech

    return str(resp)

@app.route("/status_callback", methods=['POST'])
def status_callback():
    """
    Receives status updates from Twilio (e.g., call completed, busy, no-answer).
    We'll use this to trigger the email summary.
    """
    call_sid = request.values.get('CallSid')
    call_status = request.values.get('CallStatus')

    app.logger.info(f"Call {call_sid} status: {call_status}")

    # When the call ends, generate and send the summary
    if call_status in ['completed', 'busy', 'no-answer', 'failed'] and call_sid in conversations:
        app.logger.info(f"Call {call_sid} ended. Generating summary.")
        full_transcript = "\n".join(conversations[call_sid]["transcript"])

        summary_prompt = SYSTEM_PROMPT_SUMMARIZER + full_transcript

        if GEMINI_MODEL:
            try:
                summary_response = GEMINI_MODEL.generate_content(summary_prompt).text
                app.logger.info(f"Summary for Call {call_sid}:\n{summary_response}")
                send_email_summary(call_sid, summary_response)
            except Exception as e:
                app.logger.error(f"Error generating summary with Gemini for Call {call_sid}: {e}")
                # Fallback: send raw transcript if LLM summary fails
                send_email_summary(call_sid, f"LLM Summary Failed for Call {call_sid}.\n\nFull Transcript:\n{full_transcript}")
        else:
            app.logger.warning(f"LLM not configured. Sending raw transcript as summary for Call {call_sid}.")
            send_email_summary(call_sid, f"LLM not active. Raw Transcript for Call {call_sid}:\n{full_transcript}")

        # Clean up conversation data (optional, but good practice for memory management)
        del conversations[call_sid]
        app.logger.info(f"Cleaned up conversation data for Call {call_sid}.")

    return "OK", 200 # Twilio expects a 200 OK

if __name__ == "__main__":
    app.run(debug=True, port=5000)
