from flask import Flask, request, Response
import openai
import os
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)

# Get your OpenAI API key securely from environment variable
openai.api_key = os.getenv("OPENAI_API_KEY")

@app.route("/voice", methods=["POST"])
def voice():
    caller = request.form.get("From", "someone")

    # Compose a prompt for ChatGPT
    prompt = (
        "You are a friendly virtual assistant named JC's Assistant. "
        "Answer the phone politely and let the caller know that John Constantine is not available. "
        "Offer to take a message or let them know he’ll get back to them. "
        "Keep it short, positive, and welcoming."
    )

    # Generate response using OpenAI GPT
    gpt_response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a polite phone assistant."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=60,
        temperature=0.7
    )
    reply_text = gpt_response.choices[0].message['content'].strip()

    # Respond to Twilio with TwiML
    twiml = f"""
    <Response>
        <Say voice="polly.Joanna">{reply_text}</Say>
        <Pause length="1"/>
        <Say voice="polly.Joanna">If you'd like, please leave a message after the beep.</Say>
        <Record maxLength="30" action="/handle-recording" />
        <Say voice="polly.Joanna">Thank you for calling. Goodbye!</Say>
        <Hangup/>
    </Response>
    """
    return Response(twiml, mimetype="text/xml")

@app.route("/handle-recording", methods=["POST"])
def handle_recording():
    recording_url = request.form.get("RecordingUrl", "")
    # Here you could add logic to email the recording URL or store it
    return Response("""
    <Response>
        <Say voice="polly.Joanna">Your message has been recorded. Thank you! Have a wonderful day.</Say>
        <Hangup/>
    </Response>
    """, mimetype="text/xml")

if __name__ == "__main__":
    app.run(debug=True, port=5000)
