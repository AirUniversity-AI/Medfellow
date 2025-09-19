from flask import Flask, jsonify
import os
import sys
import pymysql

app = Flask(__name__)

def get_db_connection():
    """Test database connection"""
    try:
        config = {
            'host': os.getenv("MYSQL_HOST"),
            'port': int(os.getenv("MYSQL_PORT", "3306")),
            'user': os.getenv("MYSQL_USER"),
            'password': os.getenv("MYSQL_PASSWORD"),
            'database': os.getenv("MYSQL_DATABASE"),
            'charset': 'utf8mb4',
            'connect_timeout': 10
        }
        
        connection = pymysql.connect(**config)
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        cursor.close()
        connection.close()
        
        return True, "Connected successfully"
    except Exception as e:
        return False, str(e)

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def health_check(path):
    try:
        # Check environment variables
        env_status = {
            "openai_api_key": "configured" if os.getenv("OPENAI_API_KEY") else "missing",
            "mysql_host": "configured" if os.getenv("MYSQL_HOST") else "missing",
            "mysql_password": "configured" if os.getenv("MYSQL_PASSWORD") else "missing"
        }
        
        # Test database connection
        db_connected, db_message = get_db_connection()
        
        response = {
            "status": "healthy" if db_connected else "partial",
            "database": {
                "connected": db_connected,
                "message": db_message,
                "host": os.getenv("MYSQL_HOST", "not set")
            },
            "environment": env_status,
            "python_version": sys.version,
            "deployment": "vercel_serverless"
        }
        
        status_code = 200 if db_connected else 503
        return jsonify(response), status_code
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e),
            "deployment": "vercel_serverless"
        }), 500

if __name__ == "__main__":
    app.run(debug=True)
