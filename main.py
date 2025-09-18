import json
import os
import logging
import uuid
import httpx
from flask import Flask, request, jsonify
import json
from openai import OpenAI
from dotenv import load_dotenv
from flask_cors import CORS
from q_generation_func import (
    extract_pdf_text,
    sliding_window_chunks,
    is_clinically_relevant,
    generate_mcqs_with_assistant,
    deduplicate_mcqs,
    mcqs_to_excel
)
from board_explainer import GenericBoardStyleMedicalExplainer
from werkzeug.utils import secure_filename
import cloudinary
import cloudinary.uploader
from io import BytesIO
import sys
import pymysql
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from contextlib import contextmanager

sys.stdout.reconfigure(encoding='utf-8')

# Load env and configure logging
load_dotenv()
logging.basicConfig(level=logging.INFO)

# OpenAI Configuration
API_KEY = os.getenv("OPENAI_API_KEY")

# MySQL Configuration
MYSQL_HOST = os.getenv("MYSQL_HOST", "tramway.proxy.rlwy.net")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "51549"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "railway")

if not API_KEY:
    raise ValueError("Missing OpenAI API key.")

if not MYSQL_PASSWORD:
    raise ValueError("Missing MySQL credentials.")

client = OpenAI(api_key=API_KEY)

# Initialize the board-style explainer
board_explainer = GenericBoardStyleMedicalExplainer(API_KEY)

app = Flask(__name__)
CORS(app, origins="*")

# Thread-safe storage for tasks
task_status = {}
running_tasks = {}
mcq_tasks = {}
mcqs_running_tasks = {}

# Thread locks for safe access
task_lock = Lock()
mcq_lock = Lock()

# Thread pool executor for background tasks
executor = ThreadPoolExecutor(max_workers=10)

# Database connection configuration
db_config = {
    'host': MYSQL_HOST,
    'port': MYSQL_PORT,
    'user': MYSQL_USER,
    'password': MYSQL_PASSWORD,
    'database': MYSQL_DATABASE,
    'charset': 'utf8mb4',
    'autocommit': True,
    'connect_timeout': 30
}

# Simple connection pool using thread-local storage
import threading
thread_local = threading.local()

def get_db_connection():
    """Get a database connection for the current thread"""
    if not hasattr(thread_local, 'connection'):
        thread_local.connection = pymysql.connect(**db_config)
    
    # Check if connection is still alive
    try:
        thread_local.connection.ping(reconnect=True)
    except:
        thread_local.connection = pymysql.connect(**db_config)
    
    return thread_local.connection

@contextmanager
def get_db_cursor():
    """Context manager for database operations"""
    connection = get_db_connection()
    cursor = None
    try:
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        yield cursor
        connection.commit()
    except Exception as e:
        connection.rollback()
        raise e
    finally:
        if cursor:
            cursor.close()

def execute_query(query, params=None):
    """Execute database query and return results in PHP-like format"""
    try:
        with get_db_cursor() as cursor:
            cursor.execute(query, params or ())
            
            if query.strip().upper().startswith('SELECT'):
                result = cursor.fetchall()
                return {"data": list(result)}
            else:
                return {"affected_rows": cursor.rowcount}
                
    except Exception as e:
        print(f"Database query failed: {e}")
        print(f"Query: {query}")
        print(f"Params: {params}")
        return {"error": str(e)}

@app.route("/get-remaining-question-count", methods=["POST"])
def get_remaining_question_count():
    data = request.get_json()
    category_id = int(data.get("categoryId"))
    subject_name = data.get("subjectName")
    topic_name = data.get("topicName")

    print(category_id, subject_name, topic_name)

    # Get subject ID
    query_subject = "SELECT id FROM subject WHERE categoryId = %s AND subjectName = %s"
    subject_resp = execute_query(query_subject, (category_id, subject_name))
    subject_data = subject_resp.get("data", [])
    if not subject_data:
        return jsonify({"count": 0})
    subject_id = subject_data[0]["id"]

    # Get topic ID
    query_topic = "SELECT id FROM topics WHERE subjectId = %s AND topicName = %s"
    topic_resp = execute_query(query_topic, (subject_id, topic_name))
    topic_data = topic_resp.get("data", [])
    if not topic_data:
        return jsonify({"count": 0})
    topic_id = topic_data[0]["id"]

    # Get question IDs
    query_ids = "SELECT questionId FROM topicQueRel WHERE topicId = %s"
    ids_resp = execute_query(query_ids, (topic_id,))
    question_data = ids_resp.get("data", [])
    question_ids = [str(row["questionId"]) for row in question_data]
    if not question_ids:
        return jsonify({"count": 0})

    # Count questions with NULL description
    ids_placeholders = ",".join(["%s"] * len(question_ids))
    query_count = f"SELECT COUNT(*) AS count FROM tblquestion WHERE questionId IN ({ids_placeholders}) AND (description IS NULL OR TRIM(description) = '')"

    count_resp = execute_query(query_count, question_ids)
    count_data = count_resp.get("data", [])
    count = count_data[0]["count"] if count_data else 0

    print("Count:", count)
    return jsonify({"count": count})

@app.route("/generate-category-questions", methods=["POST"])
def generate_category_questions():
    try:
        data = request.get_json()
        category_id = int(data.get("categoryId"))
        subject_name = data.get("subjectName")
        topic_name = data.get("topicName")

        print(f"[INIT TASK] categoryId={category_id}, subject={subject_name}, topic={topic_name}", flush=True)

        task_id = str(uuid.uuid4())
        
        with task_lock:
            task_status[task_id] = {"status": "started", "progress": 0, "results": [], "error": None}

        # Start background task
        future = executor.submit(process_question_generation, task_id, category_id, subject_name, topic_name)
        
        with task_lock:
            running_tasks[task_id] = future

        return jsonify({"status": "started", "taskId": task_id})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/task-status/<task_id>", methods=["GET"])
def task_status_check(task_id):
    with task_lock:
        task = task_status.get(task_id)
    if not task:
        return jsonify({"status": "not_found"}), 404
    return jsonify(task)

@app.route("/cancel-task/<task_id>", methods=["POST", "GET"])
def cancel_task(task_id):
    with task_lock:
        future = running_tasks.get(task_id)
        if future and not future.done():
            future.cancel()
            print(f"[CANCEL] Task {task_id} was cancelled by user.", flush=True)
        task_status[task_id]["status"] = "cancelled"
        task_status[task_id]["error"] = "Cancelled by user."
        running_tasks.pop(task_id, None)
    return "", 200

def process_question_generation(task_id, category_id, subject_name, topic_name):
    try:
        # Get subject ID
        query_subject = "SELECT id FROM subject WHERE categoryId = %s AND subjectName = %s"
        subject = execute_query(query_subject, (category_id, subject_name))
        if not subject.get("data"):
            raise Exception("Subject not found")
        subject_id = subject.get("data", [])[0]["id"]

        # Get topic ID
        query_topic = "SELECT id FROM topics WHERE subjectId = %s AND topicName = %s"
        topic = execute_query(query_topic, (subject_id, topic_name))
        if not topic.get("data"):
            raise Exception("Topic not found")
        topic_id = topic.get("data", [])[0]["id"]

        # Get question IDs
        query_ids = "SELECT questionId FROM topicQueRel WHERE topicId = %s"
        ids_resp = execute_query(query_ids, (topic_id,))
        question_data = ids_resp.get("data", [])
        if not isinstance(question_data, list) or not question_data:
            raise Exception("No questions found")
        question_ids = [str(row["questionId"]) for row in question_data]

        # Get questions
        ids_placeholders = ",".join(["%s"] * len(question_ids))
        query_questions = f"SELECT questionId, question, description FROM tblquestion WHERE questionId IN ({ids_placeholders}) AND (description IS NULL OR TRIM(description) = '')"

        questions_resp = execute_query(query_questions, question_ids)
        questions = questions_resp.get("data", [])

        if not questions:
            with task_lock:
                task_status[task_id] = {
                    "status": "completed",
                    "progress": 0,
                    "results": [],
                    "error": "All questions already explained."
                }
            return

        # Get options
        query_options = f"SELECT questionId, questionImageText, isCorrectAnswer FROM tblquestionoption WHERE questionId IN ({ids_placeholders})"
        options_resp = execute_query(query_options, question_ids)
        options = options_resp.get("data", [])

        label_map = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

        for idx, q in enumerate(questions, start=1):
            try:
                # Check if task was cancelled
                with task_lock:
                    if task_id not in running_tasks:
                        raise Exception("Task cancelled")

                q_opts = [opt for opt in options if opt["questionId"] == q["questionId"]]
                correct = next((opt for opt in q_opts if opt["isCorrectAnswer"] == "1"), None)

                labeled_opts = []
                correct_label = ""
                for i, opt in enumerate(q_opts):
                    label = label_map[i]
                    labeled_opts.append(opt['questionImageText'])
                    if opt["isCorrectAnswer"] == "1":
                        correct_label = label

                # Use the board explainer to generate explanation
                explanation = board_explainer.generate_simple_explanation(
                    q['question'],
                    labeled_opts,
                    correct["questionImageText"] if correct else ""
                )

                update_query = "UPDATE tblquestion SET description = %s WHERE questionId = %s"
                response = execute_query(update_query, (explanation, int(q['questionId'])))

                if response.get("error"):
                    raise Exception("DB update failed")

                with task_lock:
                    task_status[task_id]["results"].append({
                        "index": idx,
                        "questionId": q["questionId"],
                        "question": q.get("question", ""),
                        "options": [opt.get("questionImageText", "") for opt in q_opts],
                        "correctAnswer": correct["questionImageText"] if correct else None,
                        "explanation": explanation
                    })
                    task_status[task_id]["progress"] = idx

            except Exception as inner_e:
                print(f"[ERROR] Question {idx} failed {inner_e}")
                with task_lock:
                    task_status[task_id]["results"].append({
                        "index": idx,
                        "questionId": q["questionId"],
                        "error": str(inner_e)
                    })

        with task_lock:
            task_status[task_id]["status"] = "completed"
        print(f"[TASK COMPLETE] All questions processed.")

    except Exception as outer_e:
        print(f"[TASK ERROR] {outer_e}")
        with task_lock:
            task_status[task_id] = {
                "status": "failed",
                "error": str(outer_e)
            }

@app.route("/fetch-questions-by-topic", methods=["POST"])
def fetch_questions_by_topic():
    try:
        data = request.get_json()
        topic_id = data.get("topicId")
        print("Topic id is", topic_id)

        if not topic_id:
            return jsonify({"error": "Missing topicId"}), 400

        # Step 1: Fetch question IDs linked to the topic
        query_ids = "SELECT questionId FROM topicQueRel WHERE topicId = %s"
        response_ids = execute_query(query_ids, (topic_id,))

        if response_ids.get("error"):
            return jsonify({"error": "Failed to fetch question IDs"}), 500

        id_data = response_ids
        print("Raw topic-question ID data:", id_data)

        # Correct extraction of questionId values from response
        rows = id_data.get("data", [])
        question_ids = [row["questionId"] for row in rows if row.get("questionId")]
        print("Extracted question IDs:", question_ids)

        if not question_ids:
            return jsonify([])

        # Step 2: Build query for full questions
        ids_placeholders = ",".join(["%s"] * len(question_ids))
        query_questions = f"SELECT * FROM tblquestion WHERE questionId IN ({ids_placeholders})"
        print("Final question query:", query_questions)

        response_questions = execute_query(query_questions, question_ids)

        if response_questions.get("error"):
            return jsonify({"error": "Failed to fetch questions"}), 500

        questions = response_questions
        print("Fetched questions:", questions)

        return jsonify(questions)

    except Exception as e:
        print("Error:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/fetch-subjects", methods=["POST"])
def fetch_subjects():
    try:
        data = request.get_json()
        category_id = data.get("categoryId")

        if not category_id:
            return jsonify({"error": "Missing categoryId"}), 400

        # Prepare the SQL query
        sql_query = "SELECT * FROM subject WHERE categoryId = %s"

        # Make database query
        response = execute_query(sql_query, (category_id,))

        if response.get("error"):
            return jsonify({"error": "Failed to query external DB"}), 500

        # Return the response in the same format as before
        return jsonify(response)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/fetch-topics", methods=["POST"])
def fetch_topics():
    try:
        data = request.get_json()
        subject_id = data.get("subjectId")

        if not subject_id:
            return jsonify({"error": "Missing subjectId"}), 400

        sql_query = "SELECT * FROM topics WHERE subjectId = %s"

        response = execute_query(sql_query, (subject_id,))

        if response.get("error"):
            return jsonify({"error": "Failed to query external DB"}), 500

        # Return response in the same format as before
        return jsonify(response)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    try:
        # Test database connection
        test_query = "SELECT 1 as test"
        result = execute_query(test_query)

        if result.get("data") and result["data"][0]["test"] == 1:
            return jsonify({
                "status": "healthy",
                "database": "connected",
                "host": MYSQL_HOST,
                "database": MYSQL_DATABASE
            }), 200
        else:
            return jsonify({"status": "unhealthy", "database": "disconnected"}), 500

    except Exception as e:
        return jsonify({"status": "unhealthy", "database": "error", "error": str(e)}), 500

# ==================================== QUESTION GENERATION
app.config['UPLOAD_FOLDER'] = '/tmp'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Cloudinary Config
cloudinary.config(
    cloud_name="dgxolaza9",
    api_key="163384472599539",
    api_secret="V6r9rqUvsenV9VBM1SBKEZep2sM",
    secure=True
)

@app.route('/start-generate-mcqs', methods=['POST'])
def start_generate_mcqs():
    try:
        pdf = request.files.get('pdf')

        if not pdf:
            return jsonify({'error': 'No PDF uploaded'}), 400

        task_id = str(uuid.uuid4())
        with mcq_lock:
            mcq_tasks[task_id] = {
                'status': 'queued',
                'progress': 'Queued',
                'download_url': None,
                'error': None
            }

        filename = secure_filename(pdf.filename)
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{task_id}_{filename}")
        file_bytes = pdf.read()
        file_buffer = BytesIO(file_bytes)

        # Launch the background task
        future = executor.submit(save_and_process, file_buffer, task_id, pdf_path, filename)
        
        with mcq_lock:
            mcqs_running_tasks[task_id] = future

        return jsonify({'task_id': task_id}), 202

    except Exception as outer_e:
        print(f"[STARTUP ERROR] Failed to launch MCQ task: {str(outer_e)}", flush=True)
        return jsonify({'error': str(outer_e)}), 500

def save_and_process(file_buffer: BytesIO, task_id, pdf_path, filename):
    try:
        # Save PDF file to the designated path
        with open(pdf_path, 'wb') as f:
            f.write(file_buffer.read())

        # Proceed with MCQ generation
        process_mcqs_task(task_id, pdf_path, filename)

    except Exception as e:
        with mcq_lock:
            mcq_tasks[task_id]['status'] = 'error'
            mcq_tasks[task_id]['error'] = str(e)
        print(f"[MCQ TASK] {task_id} - ERROR: {str(e)}")

@app.route("/cancel-mcq-task/<task_id>", methods=["POST", "GET"])
def cancel_mcq_task(task_id):
    print("CANCEL MCQ IS CALLED")
    with mcq_lock:
        future = mcqs_running_tasks.get(task_id)
        if future and not future.done():
            future.cancel()
            print(f"[CANCEL] Task {task_id} was cancelled by user.", flush=True)
        mcq_tasks[task_id]["status"] = "cancelled"
        mcq_tasks[task_id]["error"] = "Cancelled by user."
        mcqs_running_tasks.pop(task_id, None)
    return "", 200

@app.route('/mcq-status/<task_id>', methods=['GET'])
def get_mcq_status(task_id):
    with mcq_lock:
        task_info = mcq_tasks.get(task_id)
    if not task_info:
        return jsonify({'error': 'Invalid task ID'}), 404
    return jsonify(task_info)

def process_mcqs_task(task_id, pdf_path, filename):
    try:
        with mcq_lock:
            mcq_tasks[task_id]['status'] = 'processing'
            mcq_tasks[task_id]['progress'] = 'Extracting text...'
        print(f"[MCQ TASK] {task_id} - Extracting text...")

        # Extract text from PDF
        full_text = extract_pdf_text(pdf_path)

        # Chunk the extracted text
        chunks = sliding_window_chunks(full_text, 1200, 600)

        # Check if the content is clinically relevant
        is_relevant = is_clinically_relevant(client, chunks[0])
        if not is_relevant:
            with mcq_lock:
                mcq_tasks[task_id]['status'] = 'error'
                mcq_tasks[task_id]['error'] = 'PDF is not clinically relevant'
            return

        all_mcqs = []
        for i, chunk in enumerate(chunks[:4]):
            # Check if task was cancelled
            with mcq_lock:
                if task_id not in mcqs_running_tasks:
                    print(f"[MCQ TASK] {task_id} - Detected cancellation before chunk {i + 1}.", flush=True)
                    raise Exception("Task cancelled")
                mcq_tasks[task_id]['progress'] = f'Processing chunk {i + 1} of {len(chunks)}...'
            
            print(f"[MCQ TASK] {task_id} - Processing chunk {i + 1}")

            # Generate MCQs for each chunk
            mcqs = generate_mcqs_with_assistant(client, chunk, task_id, mcqs_running_tasks)
            all_mcqs.extend(mcqs)

        # Deduplicate the generated MCQs
        with mcq_lock:
            mcq_tasks[task_id]['progress'] = 'Exporting MCQs to Excel...'
        print(f"[MCQ TASK] {task_id} - Exporting to Excel...")

        final_mcqs = deduplicate_mcqs(all_mcqs)
        temp_excel_path = os.path.join("/tmp", filename.replace('.pdf', '_mcqs.xlsx'))

        # Export MCQs to Excel
        mcqs_to_excel(final_mcqs, temp_excel_path)

        with mcq_lock:
            mcq_tasks[task_id]['progress'] = 'Uploading to Cloudinary...'
        print(f"[MCQ TASK] {task_id} - Uploading to Cloudinary...")

        # Upload the Excel file to Cloudinary
        upload_result = cloudinary.uploader.upload(
            temp_excel_path,
            resource_type="raw",
            folder="mcqs_outputs",
            public_id=filename.replace('.pdf', '_mcqs'),
            use_filename=True,
            unique_filename=False,
            overwrite=True
        )

        with mcq_lock:
            mcq_tasks[task_id]['status'] = 'completed'
            mcq_tasks[task_id]['progress'] = 'Generation complete.'
            mcq_tasks[task_id]['download_url'] = upload_result.get('secure_url')
        print(f"[MCQ TASK] {task_id} - Task completed.")

    except Exception as e:
        with mcq_lock:
            mcq_tasks[task_id]['status'] = 'error'
            mcq_tasks[task_id]['error'] = str(e)
        print(f"[MCQ TASK] {task_id} - ERROR: {str(e)}")

@app.route("/delete-description", methods=["POST"])
def delete_question_description():
    try:
        data = request.get_json()
        question_id = int(data.get("questionId"))

        if not question_id:
            return jsonify({"status": "error", "message": "Missing questionId"}), 400

        # Step 1: Check if description exists
        check_query = "SELECT description FROM tblquestion WHERE questionId = %s"
        check_response = execute_query(check_query, (question_id,))
        check_data = check_response.get("data", [])

        if not check_data or not check_data[0].get("description"):
            return jsonify({"status": "no", "message": "No description to remove."})

        # Step 2: Nullify the description
        nullify_query = "UPDATE tblquestion SET description = NULL WHERE questionId = %s"
        update_response = execute_query(nullify_query, (question_id,))

        if not update_response.get("error"):
            return jsonify({"status": "success", "message": f"Description removed for questionId={question_id}"})
        else:
            return jsonify({"status": "error", "message": "DB update failed"}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/generate-missing-descriptions", methods=["POST"])
def generate_missing_descriptions():
    try:
        print("HIT /generate-missing-descriptions", flush=True)

        task_id = str(uuid.uuid4())
        with task_lock:
            task_status[task_id] = {
                "status": "queued",
                "progress": "Queued",
                "results": [],
                "error": None
            }

        future = executor.submit(process_all_questions_without_description, task_id)
        
        with task_lock:
            running_tasks[task_id] = future

        return jsonify({"status": "started", "taskId": task_id})
    except Exception as e:
        print(f"[ROUTE ERROR] Failed to launch task: {str(e)}", flush=True)
        return jsonify({"status": "error", "error": str(e)}), 500

def batchify(iterable, size=50):
    from itertools import islice
    iterable = iter(iterable)
    while True:
        batch = list(islice(iterable, size))
        if not batch:
            break
        yield batch

@app.route("/get_all_question_count", methods=["GET"])
def get_all_question_count():
    query = "SELECT COUNT(*) AS count FROM tblquestion WHERE description IS NULL"
    resp = execute_query(query)
    return jsonify(resp)

def process_all_questions_without_description(task_id):
    print("HIT generate-missing-descriptions route")
    try:
        print(f"[TASK START] Global explanation generation task started: {task_id}")

        print("Fetching all questions with NULL descriptions...")
        query = "SELECT questionId FROM tblquestion WHERE description IS NULL"
        response = execute_query(query)
        question_ids = [int(row["questionId"]) for row in response.get("data", [])]

        if not question_ids:
            print("No questions found with NULL description. Exiting.")
            with task_lock:
                task_status[task_id] = {
                    "status": "completed",
                    "progress": 0,
                    "results": [],
                    "error": "All questions already have explanations."
                }
            return

        print(f"{len(question_ids)} question(s) to process.")
        label_map = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

        idx = 0
        for batch_ids in batchify(question_ids, size=50):
            ids_placeholders = ",".join(["%s"] * len(batch_ids))

            query_q = f"SELECT questionId, question FROM tblquestion WHERE questionId IN ({ids_placeholders})"
            response_questions = execute_query(query_q, batch_ids)
            questions = response_questions.get("data", [])

            query_opts = f"SELECT questionId, questionImageText, isCorrectAnswer FROM tblquestionoption WHERE questionId IN ({ids_placeholders})"
            response_options = execute_query(query_opts, batch_ids)
            options = response_options.get("data", [])

            for q in questions:
                idx += 1
                try:
                    # Check if task was cancelled
                    with task_lock:
                        if task_id not in running_tasks:
                            raise Exception("Task cancelled")

                    q_opts = [opt for opt in options if opt["questionId"] == q["questionId"]]
                    correct = next((opt for opt in q_opts if opt["isCorrectAnswer"] == "1"), None)

                    if not q_opts:
                        raise Exception("No options found.")

                    labeled_opts = []
                    for i, opt in enumerate(q_opts):
                        labeled_opts.append(opt['questionImageText'])

                    # Use the board explainer to generate explanation
                    explanation = board_explainer.generate_simple_explanation(
                        q['question'],
                        labeled_opts,
                        correct["questionImageText"] if correct else ""
                    )

                    update_query = "UPDATE tblquestion SET description = %s WHERE questionId = %s"
                    update_response = execute_query(update_query, (explanation, int(q['questionId'])))

                    if update_response.get("error"):
                        raise Exception("DB update failed")

                    with task_lock:
                        task_status[task_id]["results"].append({
                            "index": idx,
                            "questionId": q["questionId"],
                            "question": q["question"],
                            "options": [opt["questionImageText"] for opt in q_opts],
                            "correctAnswer": correct["questionImageText"] if correct else None,
                            "explanation": explanation
                        })
                        task_status[task_id]["progress"] = idx

                except Exception as e:
                    print(f"[ERROR] Question {idx} (ID={q['questionId']}) failed {e}")
                    with task_lock:
                        task_status[task_id]["results"].append({
                            "index": idx,
                            "questionId": q["questionId"],
                            "error": str(e)
                        })

        with task_lock:
            task_status[task_id]["status"] = "completed"
        print(f"[TASK DONE] Task {task_id} finished processing {idx} questions.")

    except Exception as outer_e:
        print(f"[FATAL TASK ERROR] Task {task_id} {outer_e}")
        with task_lock:
            task_status[task_id] = {
                "status": "failed",
                "error": str(outer_e)
            }

@app.route("/delete-question-descriptions-by-topic", methods=["POST"])
def delete_question_descriptions_by_topic():
    try:
        data = request.get_json()
        category_id = int(data.get("categoryId"))
        subject_name = data.get("subjectName")
        topic_name = data.get("topicName")

        # Step 1: Resolve subjectId
        query_subject = "SELECT id FROM subject WHERE categoryId = %s AND subjectName = %s"
        res_sub = execute_query(query_subject, (category_id, subject_name))
        subject_data = res_sub.get("data", [])
        if not subject_data:
            return jsonify({"status": "error", "message": "Subject not found"}), 404
        subject_id = subject_data[0]["id"]

        # Step 2: Resolve topicId
        query_topic = "SELECT id FROM topics WHERE subjectId = %s AND topicName = %s"
        res_topic = execute_query(query_topic, (subject_id, topic_name))
        topic_data = res_topic.get("data", [])
        if not topic_data:
            return jsonify({"status": "error", "message": "Topic not found"}), 404
        topic_id = topic_data[0]["id"]

        # Step 3: Get relevant questionIds
        query_qids = "SELECT questionId FROM topicQueRel WHERE topicId = %s"
        res_qids = execute_query(query_qids, (topic_id,))
        qid_data = res_qids.get("data", [])
        if not qid_data:
            return jsonify({"status": "error", "message": "No questions linked to this topic"}), 404
        question_ids = [str(row["questionId"]) for row in qid_data]

        # Step 4: Delete descriptions
        ids_placeholders = ",".join(["%s"] * len(question_ids))
        update_query = f"UPDATE tblquestion SET description = NULL WHERE questionId IN ({ids_placeholders})"
        res_update = execute_query(update_query, question_ids)

        if not res_update.get("error"):
            return jsonify(
                {"status": "success", "message": f"Descriptions removed from {len(question_ids)} questions."})
        else:
            return jsonify({"status": "error", "message": "Failed to update descriptions"}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/get-all-topic-question-count", methods=["POST"])
def get_all_topic_question_count():
    try:
        data = request.get_json()
        category_id = int(data.get("categoryId"))
        subject_name = data.get("subjectName")

        # Step 1: Get subject ID
        query_subject = "SELECT id FROM subject WHERE categoryId = %s AND subjectName = %s"
        res_sub = execute_query(query_subject, (category_id, subject_name))
        subject_data = res_sub.get("data", [])
        if not subject_data:
            raise Exception("Subject not found")

        subject_id = subject_data[0]["id"]

        # Step 2: Count all questions with NULL description in all topics
        query_count = """
            SELECT COUNT(*) as total FROM tblquestion q 
            JOIN topicQueRel rel ON rel.questionId = q.questionId 
            JOIN topics t ON t.id = rel.topicId 
            WHERE t.subjectId = %s AND 
            (q.description IS NULL OR TRIM(q.description) = '')
        """
        
        res_count = execute_query(query_count, (subject_id,))
        count_data = res_count.get("data", [])
        count = count_data[0]["total"] if count_data else 0

        return jsonify({"count": count})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/generate-all-topic-descriptions", methods=["POST"])
def generate_all_topic_descriptions():
    try:
        data = request.get_json()
        category_id = int(data.get("categoryId"))
        subject_name = data.get("subjectName")

        print(f"[INIT ALL TOPICS TASK] categoryId={category_id}, subject={subject_name}", flush=True)

        task_id = str(uuid.uuid4())
        with task_lock:
            task_status[task_id] = {"status": "started", "progress": 0, "results": [], "error": None}

        future = executor.submit(process_all_topic_questions, task_id, category_id, subject_name)
        
        with task_lock:
            running_tasks[task_id] = future

        return jsonify({"status": "started", "taskId": task_id})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

def process_all_topic_questions(task_id, category_id, subject_name):
    try:
        # Get subject ID
        query_subject = "SELECT id FROM subject WHERE categoryId = %s AND subjectName = %s"
        subject = execute_query(query_subject, (category_id, subject_name))
        if not subject.get("data"):
            raise Exception("Subject not found")
        subject_id = subject.get("data", [])[0]["id"]

        # Get all questions for this subject that need descriptions
        query_all_questions = """
            SELECT DISTINCT q.questionId, q.question 
            FROM tblquestion q
            JOIN topicQueRel rel ON rel.questionId = q.questionId
            JOIN topics t ON t.id = rel.topicId
            WHERE t.subjectId = %s AND (q.description IS NULL OR TRIM(q.description) = '')
        """
        
        questions_resp = execute_query(query_all_questions, (subject_id,))
        questions = questions_resp.get("data", [])

        if not questions:
            with task_lock:
                task_status[task_id] = {
                    "status": "completed",
                    "progress": 0,
                    "results": [],
                    "error": "All questions already explained."
                }
            return

        # Get all question IDs for options query
        question_ids = [q["questionId"] for q in questions]
        ids_placeholders = ",".join(["%s"] * len(question_ids))
        
        # Get options for all questions
        query_options = f"SELECT questionId, questionImageText, isCorrectAnswer FROM tblquestionoption WHERE questionId IN ({ids_placeholders})"
        options_resp = execute_query(query_options, question_ids)
        options = options_resp.get("data", [])

        label_map = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

        for idx, q in enumerate(questions, start=1):
            try:
                # Check if task was cancelled
                with task_lock:
                    if task_id not in running_tasks:
                        raise Exception("Task cancelled")

                q_opts = [opt for opt in options if opt["questionId"] == q["questionId"]]
                correct = next((opt for opt in q_opts if opt["isCorrectAnswer"] == "1"), None)

                if not q_opts:
                    raise Exception("No options found.")

                labeled_opts = []
                for i, opt in enumerate(q_opts):
                    labeled_opts.append(opt['questionImageText'])

                # Use the board explainer to generate explanation
                explanation = board_explainer.generate_simple_explanation(
                    q['question'],
                    labeled_opts,
                    correct["questionImageText"] if correct else ""
                )

                update_query = "UPDATE tblquestion SET description = %s WHERE questionId = %s"
                response = execute_query(update_query, (explanation, int(q['questionId'])))

                if response.get("error"):
                    raise Exception("DB update failed")

                with task_lock:
                    task_status[task_id]["results"].append({
                        "index": idx,
                        "questionId": q["questionId"],
                        "question": q.get("question", ""),
                        "options": [opt.get("questionImageText", "") for opt in q_opts],
                        "correctAnswer": correct["questionImageText"] if correct else None,
                        "explanation": explanation
                    })
                    task_status[task_id]["progress"] = idx

            except Exception as inner_e:
                print(f"[ERROR] All topics question {idx} failed {inner_e}")
                with task_lock:
                    task_status[task_id]["results"].append({
                        "index": idx,
                        "questionId": q["questionId"],
                        "error": str(inner_e)
                    })

        with task_lock:
            task_status[task_id]["status"] = "completed"
        print(f"[TASK COMPLETE] All topics questions processed.")

    except Exception as outer_e:
        print(f"[TASK ERROR] All topics task failed: {outer_e}")
        with task_lock:
            task_status[task_id] = {
                "status": "failed",
                "error": str(outer_e)
            }

# Entry point for Vercel
def create_app():
    return app

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)