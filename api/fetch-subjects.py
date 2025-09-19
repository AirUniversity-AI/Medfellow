from flask import Flask, request, jsonify
import os
import pymysql
import json

app = Flask(__name__)

def get_db_connection():
    """Get database connection"""
    try:
        config = {
            'host': os.getenv("MYSQL_HOST"),
            'port': int(os.getenv("MYSQL_PORT", "3306")),
            'user': os.getenv("MYSQL_USER"),
            'password': os.getenv("MYSQL_PASSWORD"),
            'database': os.getenv("MYSQL_DATABASE"),
            'charset': 'utf8mb4',
            'autocommit': True,
            'connect_timeout': 30
        }
        return pymysql.connect(**config)
    except Exception as e:
        print(f"Database connection failed: {e}")
        return None

def execute_query(query, params=None):
    """Execute database query and return results"""
    connection = get_db_connection()
    if not connection:
        return {"error": "Database connection failed"}
    
    try:
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        cursor.execute(query, params or ())
        
        if query.strip().upper().startswith('SELECT'):
            result = cursor.fetchall()
            return {"data": list(result)}
        else:
            return {"affected_rows": cursor.rowcount}
            
    except Exception as e:
        print(f"Query execution failed: {e}")
        return {"error": str(e)}
    finally:
        cursor.close()
        connection.close()

@app.route('/', methods=['GET', 'POST'])
def fetch_subjects():
    if request.method == 'GET':
        return jsonify({
            "error": "This endpoint requires POST method",
            "usage": "POST with JSON body: {\"categoryId\": 1}"
        }), 405
    
    try:
        # Handle both JSON and form data
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form.to_dict()
            
        if not data:
            return jsonify({"error": "No data provided"}), 400

        category_id = data.get("categoryId")
        
        if not category_id:
            return jsonify({"error": "Missing categoryId"}), 400

        # Convert to int if it's a string
        try:
            category_id = int(category_id)
        except (ValueError, TypeError):
            return jsonify({"error": "categoryId must be a number"}), 400

        # Query database
        sql_query = "SELECT * FROM subject WHERE categoryId = %s"
        response = execute_query(sql_query, (category_id,))

        if response.get("error"):
            return jsonify({
                "error": "Failed to query database", 
                "details": response["error"]
            }), 500

        return jsonify(response), 200

    except Exception as e:
        return jsonify({
            "error": "Internal server error",
            "details": str(e)
        }), 500

# For local testing
if __name__ == "__main__":
    app.run(debug=True)
