from flask import Flask, request, jsonify
import os
import pymysql

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
def fetch_questions_by_topic():
    if request.method == 'GET':
        return jsonify({
            "error": "This endpoint requires POST method",
            "usage": "POST with JSON body: {\"topicId\": 1}"
        }), 405
    
    try:
        # Handle both JSON and form data
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form.to_dict()
            
        if not data:
            return jsonify({"error": "No data provided"}), 400

        topic_id = data.get("topicId")
        
        if not topic_id:
            return jsonify({"error": "Missing topicId"}), 400

        # Convert to int if it's a string
        try:
            topic_id = int(topic_id)
        except (ValueError, TypeError):
            return jsonify({"error": "topicId must be a number"}), 400

        print(f"Fetching questions for topic ID: {topic_id}")

        # Step 1: Fetch question IDs linked to the topic
        query_ids = "SELECT questionId FROM topicQueRel WHERE topicId = %s"
        response_ids = execute_query(query_ids, (topic_id,))

        if response_ids.get("error"):
            return jsonify({"error": "Failed to fetch question IDs"}), 500

        print(f"Raw topic-question ID data: {response_ids}")

        # Extract question IDs from response
        rows = response_ids.get("data", [])
        question_ids = [row["questionId"] for row in rows if row.get("questionId")]
        print(f"Extracted question IDs: {question_ids}")

        if not question_ids:
            return jsonify({"data": []}), 200

        # Step 2: Build query for full questions
        ids_placeholders = ",".join(["%s"] * len(question_ids))
        query_questions = f"SELECT * FROM tblquestion WHERE questionId IN ({ids_placeholders})"
        print(f"Final question query: {query_questions}")

        response_questions = execute_query(query_questions, question_ids)

        if response_questions.get("error"):
            return jsonify({"error": "Failed to fetch questions"}), 500

        print(f"Fetched questions: {response_questions}")
        return jsonify(response_questions), 200

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({
            "error": "Internal server error",
            "details": str(e)
        }), 500

# For local testing
if __name__ == "__main__":
    app.run(debug=True)
