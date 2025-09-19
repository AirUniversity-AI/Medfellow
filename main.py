from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"message": "Hello from Flask!", "status": "working"})

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "test": "success"})
    
@app.route('/test')
def test():
    return jsonify({
        "message": "Flask is working!",
        "deployment": "success",
        "timestamp": str(time.time())
    })
    
if __name__ == "__main__":
    app.run()

