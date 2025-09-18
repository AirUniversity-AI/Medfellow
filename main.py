from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"message": "Hello from Flask!", "status": "working"})

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "test": "success"})

if __name__ == "__main__":
    app.run()
