from flask import Flask, jsonify
import os
import json

app = Flask(__name__)

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    return jsonify({
        "message": "Test endpoint is working!",
        "status": "success",
        "deployment": "vercel_serverless",
        "path": path,
        "environment_check": {
            "openai_key_present": bool(os.getenv("OPENAI_API_KEY")),
            "mysql_host_present": bool(os.getenv("MYSQL_HOST"))
        }
    })

# Vercel serverless function handler
def handler(request):
    return app(request.environ, lambda status, headers: None)

# For local testing
if __name__ == "__main__":
    app.run(debug=True)
