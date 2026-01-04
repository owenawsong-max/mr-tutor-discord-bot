from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route('/')
def home():
    return 'Bot is alive!', 200

def run():
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

def start():
    """Start the Flask server in a background thread"""
    server = Thread(target=run, daemon=True)
    server.start()
    print('✅ Keep-alive server started on port 8080')
