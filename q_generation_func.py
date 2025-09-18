import os
import fitz  # PyMuPDF
import pandas as pd
import json
import time

# Extract text from PDF
def extract_pdf_text(file_path):
    doc = fitz.open(file_path)
    full_text = ""
    for page in doc:
        text = page.get_text()
        if text.strip():
            full_text += text.strip() + " "
    return full_text

# Sliding window text chunking
def sliding_window_chunks(text, window_size=1200, step_size=600):
    words = text.split()
    return [" ".join(words[i:i + window_size]) for i in range(0, len(words) - window_size + 1, step_size)]

# Remove duplicate questions
def deduplicate_mcqs(mcq_list):
    seen = set()
    unique_mcqs = []
    for block in mcq_list:
        topic = block.get("topic") or block.get("temat")
        questions = []
        for q in block.get("questions", []):
            if q["question"] not in seen:
                seen.add(q["question"])
                questions.append(q)
        if questions:
            unique_mcqs.append({"temat": topic, "questions": questions})
    return unique_mcqs

# Save to Excel
def mcqs_to_excel(mcq_list, output_path):
    rows = []
    for mcq_block in mcq_list:
        topic = mcq_block.get("topic") or mcq_block.get("temat", "")
        for question_data in mcq_block.get("questions", []):
            rows.append({
                "Temat": topic,
                "Pytanie": question_data.get("question", ""),
                "Opcja A": question_data.get("options", {}).get("A", ""),
                "Opcja B": question_data.get("options", {}).get("B", ""),
                "Opcja C": question_data.get("options", {}).get("C", ""),
                "Opcja D": question_data.get("options", {}).get("D", ""),
                "Poprawna OdpowiedÃ…Âº": question_data.get("answer", ""),
                "WyjaÃ…â€ºnienie": question_data.get("explanation", "")
            })
    df = pd.DataFrame(rows)
    df.to_excel(output_path, index=False)

def extract_title_from_text(text):
    """Extract title from text using various strategies"""
    # Strategy 1: Look for markdown headings
    for line in text.split("\n"):
        if line.strip().startswith("#"):
            return line.strip().replace("#", "").strip()
    
    # Strategy 2: Look for common medical topic patterns
    lines = text.split("\n")[:10]  # Check first 10 lines
    for line in lines:
        line = line.strip()
        if len(line) > 10 and len(line) < 100:
            # Look for title-like patterns
            if any(word in line.lower() for word in ['chapter', 'section', 'topic', 'disease', 'syndrome', 'treatment', 'diagnosis']):
                return line
    
    # Strategy 3: Use first substantial line as fallback
    for line in lines:
        line = line.strip()
        if len(line) > 20 and len(line) < 150:
            return line
    
    return "Unknown Topic"

def generate_mcqs_with_assistant(client, text, task_id, mcqs_running_tasks, min_required=1, max_attempts=3):
    """
    Generate MCQs using OpenAI Chat Completions instead of Assistants API
    Updated for Flask synchronous operation
    """
    
    # System prompt for MCQ generation
    system_prompt = """You are a medical education expert specializing in creating high-quality multiple-choice questions (MCQs) from clinical content. 

Your task is to:
1. Analyze the provided medical text
2. Identify key clinical concepts that would make good exam questions
3. Generate 2-4 high-quality MCQs with 4 options each
4. Provide clear explanations for correct answers
5. Extract a relevant topic name from the content

Requirements:
- Questions should test clinical knowledge, not memorization
- Options should be plausible and realistic
- Include both correct and incorrect but reasonable distractors
- Explanations should be educational and evidence-based
- Focus on clinically relevant scenarios

Return your response as a JSON object with this exact format:
{
  "topic": "Extracted topic name from the text",
  "questions": [
    {
      "question": "Question text here",
      "options": {
        "A": "First option",
        "B": "Second option", 
        "C": "Third option",
        "D": "Fourth option"
      },
      "answer": "A",
      "explanation": "Detailed explanation of why A is correct and others are wrong"
    }
  ]
}

CRITICAL: Return ONLY the JSON object, no additional text or formatting."""

    for attempt in range(max_attempts):
        # Check if task was cancelled (thread-safe check)
        from threading import Lock
        with Lock():
            if task_id not in mcqs_running_tasks:
                print(f"[MCQ TASK] {task_id} - Detected cancellation during generation attempt {attempt + 1}", flush=True)
                raise Exception("Task cancelled")
        
        try:
            print(f"[MCQ GENERATION] Attempt {attempt + 1} of {max_attempts}")
            
            # Create the user prompt
            user_prompt = f"""Generate medical MCQs from the following text:

{text}

Please create 2-4 high-quality multiple-choice questions based on the key clinical concepts in this text. Follow the JSON format specified in the system message."""

            # Make API call to chat completions
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=4000,
                response_format={"type": "json_object"}  # Enforce JSON response
            )
            
            # Parse the response
            response_content = response.choices[0].message.content.strip()
            print(f"[MCQ GENERATION] Raw response length: {len(response_content)} chars")
            
            try:
                parsed_quiz = json.loads(response_content)
                
                # Validate the response structure
                if not isinstance(parsed_quiz, dict):
                    raise ValueError("Response is not a JSON object")
                
                if "questions" not in parsed_quiz:
                    raise ValueError("No 'questions' key in response")
                
                if not isinstance(parsed_quiz["questions"], list):
                    raise ValueError("'questions' is not a list")
                
                if len(parsed_quiz["questions"]) == 0:
                    raise ValueError("No questions generated")
                
                # Ensure topic is present
                if "topic" not in parsed_quiz or not parsed_quiz["topic"]:
                    parsed_quiz["topic"] = extract_title_from_text(text)
                
                # Validate each question structure
                for i, question in enumerate(parsed_quiz["questions"]):
                    required_keys = ["question", "options", "answer", "explanation"]
                    for key in required_keys:
                        if key not in question:
                            raise ValueError(f"Question {i+1} missing required key: {key}")
                    
                    # Validate options structure
                    if not isinstance(question["options"], dict):
                        raise ValueError(f"Question {i+1} options must be a dictionary")
                    
                    expected_options = ["A", "B", "C", "D"]
                    for opt in expected_options:
                        if opt not in question["options"]:
                            raise ValueError(f"Question {i+1} missing option {opt}")
                
                print(f"[MCQ GENERATION] Successfully generated {len(parsed_quiz['questions'])} questions")
                return [parsed_quiz]
                
            except json.JSONDecodeError as je:
                print(f"[MCQ GENERATION] JSON decode error on attempt {attempt + 1}: {je}")
                print(f"[MCQ GENERATION] Raw response: {response_content[:500]}...")
                
            except ValueError as ve:
                print(f"[MCQ GENERATION] Validation error on attempt {attempt + 1}: {ve}")
                
        except Exception as e:
            print(f"[MCQ GENERATION] API error on attempt {attempt + 1}: {e}")
        
        # Wait before retry if not the last attempt
        if attempt < max_attempts - 1:
            print(f"[MCQ GENERATION] Waiting 2 seconds before retry...")
            time.sleep(2)

    print(f"[MCQ GENERATION] Failed to generate MCQs after {max_attempts} attempts")
    return []

def is_clinically_relevant(client, text):
    """
    Enhanced clinical relevance checker using chat completions
    """
    prompt = f"""Analyze the following text to determine if it contains clinically relevant medical content suitable for creating medical education questions.

Text to analyze:
{text[:2000]}

Criteria for clinical relevance:
- Contains medical terminology, procedures, or clinical concepts
- Discusses patient care, diagnosis, treatment, or medical procedures
- Includes pathophysiology, pharmacology, or clinical decision-making
- Contains information that would be valuable for medical education

Respond with only "YES" if the text is clinically relevant for medical education, or "NO" if it is not.
Do not include any explanation, just YES or NO."""

    try:
        print("Checking clinical relevance...")
        
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a medical education expert who determines if content is suitable for creating medical exam questions. Respond only with YES or NO."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0,
            max_tokens=10
        )

        answer = response.choices[0].message.content.strip().upper()
        print(f"Clinical relevance check result: {answer}")
        
        return answer == "YES"
        
    except Exception as e:
        print(f"Error in clinical relevance check: {e}")
        # Default to True to avoid blocking content unnecessarily
        return True