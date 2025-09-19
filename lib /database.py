import os
import pymysql
import threading
from contextlib import contextmanager

# Thread-local storage for database connections
thread_local = threading.local()

def get_db_config():
    """Get database configuration from environment variables"""
    return {
        'host': os.getenv("MYSQL_HOST"),
        'port': int(os.getenv("MYSQL_PORT", "3306")),
        'user': os.getenv("MYSQL_USER"),
        'password': os.getenv("MYSQL_PASSWORD"),
        'database': os.getenv("MYSQL_DATABASE"),
        'charset': 'utf8mb4',
        'autocommit': True,
        'connect_timeout': 30
    }

def get_db_connection():
    """Get a database connection for the current thread"""
    # Check if all required environment variables are set
    config = get_db_config()
    if not all([config['host'], config['user'], config['password'], config['database']]):
        print("Missing required database environment variables")
        return None
    
    try:
        # Use thread-local storage for connections
        if not hasattr(thread_local, 'connection') or thread_local.connection is None:
            thread_local.connection = pymysql.connect(**config)
        
        # Check if connection is still alive
        thread_local.connection.ping(reconnect=True)
        return thread_local.connection
        
    except Exception as e:
        print(f"Database connection failed: {e}")
        # Try to create a new connection
        try:
            thread_local.connection = pymysql.connect(**config)
            return thread_local.connection
        except Exception as e2:
            print(f"Failed to create new database connection: {e2}")
            return None

@contextmanager
def get_db_cursor():
    """Context manager for database operations"""
    connection = get_db_connection()
    if not connection:
        yield None
        return
        
    cursor = None
    try:
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        yield cursor
        connection.commit()
    except Exception as e:
        if connection:
            connection.rollback()
        print(f"Database operation failed: {e}")
        raise e
    finally:
        if cursor:
            cursor.close()

def execute_query(query, params=None):
    """Execute database query and return results in standardized format"""
    try:
        with get_db_cursor() as cursor:
            if not cursor:
                return {"error": "Database connection failed"}
                
            cursor.execute(query, params or ())
            
            if query.strip().upper().startswith('SELECT'):
                result = cursor.fetchall()
                return {"data": list(result)}
            else:
                return {"affected_rows": cursor.rowcount}
                
    except Exception as e:
        print(f"Query execution failed: {e}")
        print(f"Query: {query}")
        print(f"Params: {params}")
        return {"error": str(e)}

def test_db_connection():
    """Test database connectivity and return status"""
    try:
        connection = get_db_connection()
        if not connection:
            return False, "Failed to establish connection"
        
        with get_db_cursor() as cursor:
            if not cursor:
                return False, "Failed to create cursor"
            
            cursor.execute("SELECT 1 as test")
            result = cursor.fetchone()
            
            if result and result['test'] == 1:
                return True, "Database connection successful"
            else:
                return False, "Database query failed"
                
    except Exception as e:
        return False, f"Database test failed: {str(e)}"

def get_database_info():
    """Get basic database information for health checks"""
    try:
        config = get_db_config()
        connection_status, message = test_db_connection()
        
        return {
            "host": config.get('host', 'not set'),
            "database": config.get('database', 'not set'),
            "port": config.get('port', 'not set'),
            "user": config.get('user', 'not set'),
            "connected": connection_status,
            "message": message
        }
    except Exception as e:
        return {
            "connected": False,
            "error": str(e)
        }

def close_db_connection():
    """Close the current thread's database connection"""
    try:
        if hasattr(thread_local, 'connection') and thread_local.connection:
            thread_local.connection.close()
            thread_local.connection = None
    except Exception as e:
        print(f"Error closing database connection: {e}")

# Utility functions for common database operations
def get_subjects_by_category(category_id):
    """Get all subjects for a given category"""
    query = "SELECT * FROM subject WHERE categoryId = %s"
    return execute_query(query, (category_id,))

def get_topics_by_subject(subject_id):
    """Get all topics for a given subject"""
    query = "SELECT * FROM topics WHERE subjectId = %s"
    return execute_query(query, (subject_id,))

def get_questions_by_topic(topic_id):
    """Get all questions for a given topic"""
    # First get question IDs linked to the topic
    query_ids = "SELECT questionId FROM topicQueRel WHERE topicId = %s"
    ids_result = execute_query(query_ids, (topic_id,))
    
    if ids_result.get("error") or not ids_result.get("data"):
        return ids_result
    
    # Extract question IDs
    question_ids = [row["questionId"] for row in ids_result["data"]]
    
    if not question_ids:
        return {"data": []}
    
    # Get full question details
    ids_placeholders = ",".join(["%s"] * len(question_ids))
    query_questions = f"SELECT * FROM tblquestion WHERE questionId IN ({ids_placeholders})"
    return execute_query(query_questions, question_ids)

def get_question_count_by_topic(category_id, subject_name, topic_name):
    """Get count of questions needing descriptions for a specific topic"""
    try:
        # Get subject ID
        subject_result = execute_query(
            "SELECT id FROM subject WHERE categoryId = %s AND subjectName = %s",
            (category_id, subject_name)
        )
        
        if not subject_result.get("data"):
            return {"count": 0, "error": "Subject not found"}
        
        subject_id = subject_result["data"][0]["id"]
        
        # Get topic ID
        topic_result = execute_query(
            "SELECT id FROM topics WHERE subjectId = %s AND topicName = %s",
            (subject_id, topic_name)
        )
        
        if not topic_result.get("data"):
            return {"count": 0, "error": "Topic not found"}
        
        topic_id = topic_result["data"][0]["id"]
        
        # Get question IDs for this topic
        ids_result = execute_query(
            "SELECT questionId FROM topicQueRel WHERE topicId = %s",
            (topic_id,)
        )
        
        if not ids_result.get("data"):
            return {"count": 0}
        
        question_ids = [str(row["questionId"]) for row in ids_result["data"]]
        
        # Count questions with NULL description
        ids_placeholders = ",".join(["%s"] * len(question_ids))
        count_query = f"SELECT COUNT(*) AS count FROM tblquestion WHERE questionId IN ({ids_placeholders}) AND (description IS NULL OR TRIM(description) = '')"
        
        count_result = execute_query(count_query, question_ids)
        
        if count_result.get("data"):
            return {"count": count_result["data"][0]["count"]}
        else:
            return {"count": 0, "error": count_result.get("error", "Unknown error")}
            
    except Exception as e:
        return {"count": 0, "error": str(e)}
