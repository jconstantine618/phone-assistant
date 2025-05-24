from flask import Flask, request, Response
import openai
import os
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)

# Get your OpenAI API key securely from environment variable
openai.api_key = os.getenv("OPENAI_API_KEY")

# Toggle this to False to use a hardcoded reply and skip OpenAI while testing
USE_OPENAI = True

@app.route("/voice", methods=["POST"])
def voice():
    print("Received a POST to /voice")
    caller = request.form.get("From", "someone")
    print(f"Call from: {caller}")

    prompt = (
        "You are a friendly virtual assistant named JC's Assistant. "
        "Answer the phone politely and let the caller know that John Constantine is not available. "
        "Offer to take a message or let them know he’ll get back to them. "
        "Keep it short, positive, and welcoming."
    )

    reply_text = ""
    if USE_OPENAI:
        try:
            print("About to call OpenAI API")
            gpt_response = openai.ChatCompletion.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a polite phone assistant."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=60,
                temperature=0.7
            )
            print("OpenAI call complete")
            reply_text = gpt_response.choices[0].message['content'].strip()
            print("AI Response:", reply_text)
        except Exception as e:
            print("OpenAI API ERROR:", e)
            reply_text = (
                "Sorry, I'm having trouble connecting to the assistant service right now. "
                "Please try again later or leave a message after the beep."
            )
    else:
        reply_text = (
            "Hello! This is John's assistant. John is not available right now. "
            "Please leave a message after the beep and he'll get back to you soon. Thank you for calling!"
        )

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
    print("Returning TwiML to Twilio.")
    return Response(twiml, mimetype="text/xml")

@app.route("/handle-recording", methods=["POST"])
def handle_recording():
    print("Received a POST to /handle-recording")
    recording_url = request.form.get("RecordingUrl", "")
    print(f"Recording URL: {recording_url}")
    return Response("""
    <Response>
        <Say voice="polly.Joanna">Your message has been recorded. Thank you! Have a wonderful day.</Say>
        <Hangup/>
    </Response>
    """, mimetype="text/xml")

if __name__ == "__main__":
    app.run(debug=True, port=5001)
