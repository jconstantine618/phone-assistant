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
# This line should be at the very top to ensure environment variables are loaded before config
load_dotenv()

# --- Configuration ---
# Twilio credentials are loaded from environment variables for security.
# Make sure these are set in your .env file locally, and in your deployment environment (e.g., Heroku, Google Cloud Run).
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_CLIENT = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Google Gemini API key.
# It's crucial to have this set for the LLM functionality.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # Using 'gemini-1.5-flash' for lower latency, suitable for real-time voice applications.
    GEMINI_MODEL = genai.GenerativeModel('gemini-1.5-flash')
else:
    # Log a warning if the API key is missing. The LLM features will not work.
    print("Warning: GEMINI_API_KEY not set. LLM functionality will be disabled.")
    GEMINI_MODEL = None

# Email configuration for sending summaries.
# Ensure these are set in your .env file or deployment environment.
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD") # For Gmail, use an App Password if 2FA is enabled.
RECEIVER_EMAIL = os.getenv("RECEIVER_EMAIL")
SMTP_SERVER = "smtp.gmail.com" # Standard SMTP server for Gmail.
SMTP_PORT = 587 # Standard port for TLS encryption.

# Initialize the Flask application.
app = Flask(__name__)
# Set the logging level for the Flask app to INFO to see useful messages.
app.logger.setLevel(logging.INFO)

# Global dictionary to store conversation context for each active call.
# In a production environment, this should be replaced with a persistent
# database (e.g., Redis, PostgreSQL, Firestore) to handle multiple concurrent
# calls robustly and prevent data loss if the server restarts.
conversations = {}

# --- LLM Prompts ---
# This prompt defines the persona and initial instructions for the LLM acting as a receptionist.
SYSTEM_PROMPT_RECEPTIONIST = """
You are a professional, polite, and efficient virtual receptionist named "Your Virtual Assistant" for [Your Name/Company Name].
Your primary goal is to gather information from callers, summarize their requests concisely, and ensure they feel heard.
Do not attempt to resolve complex issues directly or transfer calls.
Ask clarifying questions only when necessary to get the required information (caller's name, reason for calling, contact info).
Keep your responses brief and to the point.
Start by greeting the caller and asking the purpose of their call.
"""

# This prompt instructs the LLM on how to summarize the conversation for the email.
SYSTEM_PROMPT_SUMMARIZER = """
Summarize the following phone call conversation into a concise, actionable email for [Your Name].
Include the caller's name, their reason for calling, any contact details provided, and key actions needed.
Format it clearly for an email body.

Conversation:
"""

# --- Helper Functions ---

def send_email_summary(call_sid, summary_content):
    """
    Sends an email with the conversation summary to the RECEIVER_EMAIL.
    Logs errors if email credentials are not configured or sending fails.
    """
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECEIVER_EMAIL:
        app.logger.error("Email credentials or receiver email are not fully configured. Cannot send email.")
        return

    # Create a multipart message for the email.
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = f"Phone Chatbot Summary for Call {call_sid}"

    # Attach the summary content as plain text.
    msg.attach(MIMEText(summary_content, 'plain'))

    try:
        # Establish a secure SMTP connection.
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()  # Upgrade the connection to a secure encrypted SSL/TLS connection.
            server.login(SENDER_EMAIL, SENDER_PASSWORD) # Log in to the SMTP server.
            server.send_message(msg) # Send the email.
        app.logger.info(f"Email summary for Call {call_sid} sent successfully to {RECEIVER_EMAIL}!")
    except Exception as e:
        # Log any errors during email sending.
        app.logger.error(f"Failed to send email for Call {call_sid}: {e}")
        app.logger.error("Please check sender email/password and ensure an App Password is used for Gmail if 2FA is on.")

# --- Flask Routes ---

@app.route("/voice", methods=['GET', 'POST'])
def voice():
    """
    Handles incoming calls from Twilio.
    This is the initial webhook endpoint Twilio hits when a call is received.
    It provides the initial greeting and sets up the first speech gathering.
    """
    resp = VoiceResponse()
    call_sid = request.values.get('CallSid') # Unique ID for the call provided by Twilio.

    # Initialize conversation context for this specific call SID if it's a new call.
    if call_sid not in conversations:
        conversations[call_sid] = {
            "transcript": [], # Stores the full text transcript of the conversation.
            # LLM history for maintaining conversational context with the Gemini model.
            "llm_history": [{"role": "user", "parts": SYSTEM_PROMPT_RECEPTIONIST}]
        }
        app.logger.info(f"Starting new call: {call_sid}")
        initial_greeting = "Hello, thank you for calling. Please state the purpose of your call."
        resp.say(initial_greeting) # Twilio will speak this message.
        conversations[call_sid]["transcript"].append(f"Bot: {initial_greeting}") # Add to transcript.

    # Use Twilio's <Gather> verb to collect speech input from the caller.
    # input='speech' tells Twilio to listen for speech.
    # action specifies the URL to send the transcribed speech to.
    # timeout specifies how long to wait for speech to start.
    # speechTimeout='auto' tells Twilio to automatically detect the end of speech.
    resp.gather(
        input='speech',
        action=f'/gather_input?CallSid={call_sid}', # Callback URL after speech is gathered.
        timeout=3, # Wait 3 seconds for speech to begin.
        speechTimeout='auto' # Automatically detect end of speech.
    )
    return str(resp) # Return the TwiML XML.

@app.route("/gather_input", methods=['POST'])
def gather_input():
    """
    Receives transcribed speech from Twilio and processes it with the LLM.
    This endpoint is called by Twilio after it has gathered speech from the caller.
    """
    resp = VoiceResponse()
    call_sid = request.values.get('CallSid')
    speech_result = request.values.get('SpeechResult') # The transcribed text from the caller.
    call_status = request.values.get('CallStatus') # Current status of the call (e.g., 'in-progress', 'completed').

    # Basic error handling: if call_sid is not found, something went wrong.
    if call_sid not in conversations:
        app.logger.warning(f"No conversation found for CallSid: {call_sid}. Ending call.")
        resp.say("I'm sorry, an error occurred. Please try again.")
        resp.hangup() # Hang up the call.
        return str(resp)

    conversation_data = conversations[call_sid]
    current_transcript = conversation_data["transcript"]
    llm_history = conversation_data["llm_history"]

    app.logger.info(f"Call {call_sid} - Received speech: {speech_result}")
    current_transcript.append(f"Caller: {speech_result}") # Add caller's speech to transcript.

    # Process with LLM if speech was detected and Gemini is configured.
    if speech_result and GEMINI_MODEL:
        try:
            # Add the caller's input to the LLM's conversation history.
            llm_history.append({"role": "user", "parts": speech_result})

            # Start a chat session with the LLM using the current history.
            chat_session = GEMINI_MODEL.start_chat(history=llm_history)
            # Send the current speech result to the LLM and get its response.
            llm_response = chat_session.send_message(speech_result).text
            app.logger.info(f"Call {call_sid} - LLM response: {llm_response}")

            # Add the LLM's response to the history for the next turn.
            llm_history.append({"role": "model", "parts": llm_response})
            current_transcript.append(f"Bot: {llm_response}") # Add bot's response to transcript.

            resp.say(llm_response) # Twilio will speak the LLM's response.
            # Continue gathering more input for a multi-turn conversation.
            resp.gather(
                input='speech',
                action=f'/gather_input?CallSid={call_sid}',
                timeout=3,
                speechTimeout='auto'
            )
        except Exception as e:
            # Log errors if LLM API call fails.
            app.logger.error(f"Error calling Gemini API for Call {call_sid}: {e}")
            resp.say("I'm sorry, I'm having trouble understanding right now. Please try again or hang up.")
            resp.hangup() # End call on critical LLM error.
    elif not GEMINI_MODEL:
        # Fallback if LLM is not configured.
        app.logger.warning(f"LLM not configured. Only transcribing for Call {call_sid}.")
        resp.say("Thank you. I have received your message.")
        resp.hangup()
    else:
        # If no speech was detected (e.g., caller was silent or hung up).
        app.logger.info(f"Call {call_sid} - No speech detected or caller hung up.")
        resp.say("Thank you for calling. Goodbye.")
        resp.hangup() # End the call.

    return str(resp) # Return the TwiML XML.

@app.route("/status_callback", methods=['POST'])
def status_callback():
    """
    Receives status updates from Twilio (e.g., call completed, busy, no-answer).
    This endpoint is used to trigger the email summary generation and sending.
    """
    call_sid = request.values.get('CallSid')
    call_status = request.values.get('CallStatus') # The status of the call.

    app.logger.info(f"Call {call_sid} status: {call_status}")

    # If the call has ended and we have conversation data for it.
    if call_status in ['completed', 'busy', 'no-answer', 'failed'] and call_sid in conversations:
        app.logger.info(f"Call {call_sid} ended. Generating summary.")
        full_transcript = "\n".join(conversations[call_sid]["transcript"]) # Join all transcript parts.

        # Construct the prompt for the LLM to summarize the conversation.
        summary_prompt = SYSTEM_PROMPT_SUMMARIZER + full_transcript

        if GEMINI_MODEL:
            try:
                # Generate the summary using the LLM.
                summary_response = GEMINI_MODEL.generate_content(summary_prompt).text
                app.logger.info(f"Summary for Call {call_sid}:\n{summary_response}")
                send_email_summary(call_sid, summary_response) # Send the LLM-generated summary.
            except Exception as e:
                # Log errors if LLM summary generation fails.
                app.logger.error(f"Error generating summary with Gemini for Call {call_sid}: {e}")
                # Fallback: send the raw transcript if LLM summary fails.
                send_email_summary(call_sid, f"LLM Summary Failed for Call {call_sid}.\n\nFull Transcript:\n{full_transcript}")
        else:
            # Fallback if LLM is not configured.
            app.logger.warning(f"LLM not configured. Sending raw transcript as summary for Call {call_sid}.")
            send_email_summary(call_sid, f"LLM not active. Raw Transcript for Call {call_sid}:\n{full_transcript}")

        # Clean up conversation data to free up memory.
        # This is important for long-running applications.
        del conversations[call_sid]
        app.logger.info(f"Cleaned up conversation data for Call {call_sid}.")

    return "OK", 200 # Twilio expects a 200 OK response.

# This block ensures the Flask development server runs only when the script is executed directly.
# When deploying with a WSGI server (like Gunicorn for Heroku, or Streamlit's internal server),
# this block should NOT be executed.
if __name__ == "__main__":
    # When running locally for development, uncomment the line below.
    # When deploying to Streamlit Cloud or other production environments, keep it commented out.
    # Streamlit or your WSGI server will handle starting the application.
    # app.run(debug=True, port=5000)
    pass # 'pass' is a placeholder if the line above is commented out.

