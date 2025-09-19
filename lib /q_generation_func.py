import os
import fitz  # PyMuPDF
import pandas as pd
import json
import time
from openai import OpenAI

def extract_pdf_text(file_path):
    """Extract text from PDF file"""
    try:
        doc = fitz.open(file_path)
        full_text = ""
        for page in doc:
            text = page.get_text()
            if text.strip():
                full_text += text.strip() + " "
        doc.close()
        return full_text
    except Exception as e:
        print(f"Error extracting PDF text: {e}")
        return ""

def extract_pdf_text_from_bytes(pdf_bytes):
    """Extract text from PDF bytes (for file uploads)"""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        full_text = ""
        for page in doc:
            text = page.get_text()
            if text.strip():
                full_text += text.strip() + " "
        doc.close()
        return full_text
    except Exception as e:
        print(f"Error extracting PDF text from bytes: {e}")
        return ""

def sliding_window_chunks(text, window_size=1200, step_size=600):
    """Split text into overlapping chunks using sliding window"""
    if not text or not text.strip():
        return []
    
    words = text.split()
    if len(words) <= window_size:
        return [text]
    
    chunks = []
    for i in range(0, len(words) - window_size + 1, step_size):
        chunk = " ".join(words[i:i + window_size])
        chunks.append(chunk)
    
    return chunks

def deduplicate_mcqs(mcq_list):
    """Remove duplicate questions from MCQ list"""
    if not mcq_list:
        return []
    
    seen = set()
    unique_mcqs = []
    
    for block in mcq_list:
        if not isinstance(block, dict):
            continue
            
        topic = block.get("topic") or block.get("temat", "Unknown Topic")
        questions = block.get("questions", [])
        
        if not questions:
            continue
        
        unique_questions = []
        for q in questions:
            if not isinstance(q, dict):
                continue
            
            question_text = q.get("question", "")
            if question_text and question_text not in seen:
                seen.add(question_text)
                unique_questions.append(q)
        
        if unique_questions:
            unique_mcqs.append({"temat": topic, "questions": unique_questions})
    
    return unique_mcqs

def mcqs_to_excel(mcq_list, output_path):
    """Save MCQs to Excel file"""
    if not mcq_list:
        # Create empty Excel file
        df = pd.DataFrame(columns=["Temat", "Pytanie", "Opcja A", "Opcja B", "Opcja C", "Opcja D", "Poprawna OdpowiedÅº", "WyjaÅ›nienie"])
        df.to_excel(output_path, index=False)
        return
    
    rows = []
    for mcq_block in mcq_list:
        if not isinstance(mcq_block, dict):
            continue
            
        topic = mcq_block.get("topic") or mcq_block.get("temat", "")
        questions = mcq_block.get("questions", [])
        
        for question_data in questions:
            if not isinstance(question_data, dict):
                continue
                
            options = question_data.get("options", {})
            rows.append({
                "Temat": topic,
                "Pytanie": question_data.get("question", ""),
                "Opcja A": options.get("A", ""),
                "Opcja B": options.get("B", ""),
                "Opcja C": options.get("C", ""),
                "Opcja D": options.get("D", ""),
                "Poprawna OdpowiedÅº": question_data.get("answer", ""),
                "WyjaÅ›nienie": question_data.get("explanation", "")
            })
    
    df = pd.DataFrame(rows)
    df.to_excel(output_path, index=False)

def extract_title_from_text(text):
    """Extract title from text using various strategies"""
    if not text or not text.strip():
        return "Unknown Topic"
    
    # Strategy 1: Look for markdown headings
    lines = text.split("\n")[:10]  # Check first 10 lines
    for line in lines:
        line = line.strip()
        if line.startswith("#"):
            return line.replace("#", "").strip()
    
    # Strategy 2: Look for common medical topic patterns
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
    
    return "Medical Topic"

def generate_mcqs_with_assistant(client, text, max_attempts=2):
    """
    Generate MCQs using OpenAI Chat Completions
    Simplified version for serverless environments
    """
    
    if not text or not text.strip():
        return []
    
    # System prompt for MCQ generation
    system_prompt = """You are a medical education expert specializing in creating high-quality multiple-choice questions (MCQs) from clinical content. 

Your task is to:
1. Analyze the provided medical text
2. Identify key clinical concepts that would make good exam questions
3. Generate 2-3 high-quality MCQs with 4 options each
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
        try:
            print(f"[MCQ GENERATION] Attempt {attempt + 1} of {max_attempts}")
            
            # Create the user prompt
            user_prompt = f"""Generate medical MCQs from the following text:

{text[:3000]}  # Limit text length for serverless

Please create 2-3 high-quality multiple-choice questions based on the key clinical concepts in this text. Follow the JSON format specified in the system message."""

            # Make API call to chat completions
            response = client.chat.completions.create(
                model="gpt-4o-mini",  # Using mini for faster serverless response
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=2000,  # Reduced for serverless
                timeout=30,       # 30 second timeout
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
                            question["options"][opt] = f"Option {opt} not provided"
                
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

def is_clinically_relevant(client, text, max_chars=1500):
    """
    Enhanced clinical relevance checker using chat completions
    Simplified for serverless environments
    """
    if not text or not text.strip():
        return False
    
    # Limit text length for faster processing
    text_sample = text[:max_chars]
    
    prompt = f"""Analyze the following text to determine if it contains clinically relevant medical content suitable for creating medical education questions.

Text to analyze:
{text_sample}

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
            model="gpt-4o-mini",  # Using mini for faster serverless response
            messages=[
                {"role": "system", "content": "You are a medical education expert who determines if content is suitable for creating medical exam questions. Respond only with YES or NO."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0,
            max_tokens=10,
            timeout=15  # Reduced timeout for serverless
        )

        answer = response.choices[0].message.content.strip().upper()
        print(f"Clinical relevance check result: {answer}")
        
        return answer == "YES"
        
    except Exception as e:
        print(f"Error in clinical relevance check: {e}")
        # Default to True to avoid blocking content unnecessarily
        return True

def create_openai_client(api_key=None):
    """Create OpenAI client with error handling"""
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        raise ValueError("OpenAI API key is required")
    
    return OpenAI(api_key=api_key)

def process_pdf_for_mcqs(pdf_path_or_bytes, api_key=None, max_chunks=4):
    """
    Complete pipeline for processing PDF and generating MCQs
    Optimized for serverless environments
    """
    try:
        client = create_openai_client(api_key)
        
        # Extract text from PDF
        if isinstance(pdf_path_or_bytes, (bytes, bytearray)):
            full_text = extract_pdf_text_from_bytes(pdf_path_or_bytes)
        else:
            full_text = extract_pdf_text(pdf_path_or_bytes)
        
        if not full_text or len(full_text.strip()) < 100:
            return {"error": "Could not extract sufficient text from PDF"}
        
        # Check clinical relevance
        if not is_clinically_relevant(client, full_text[:2000]):
            return {"error": "PDF content is not clinically relevant for medical education"}
        
        # Create chunks
        chunks = sliding_window_chunks(full_text, 1200, 600)
        if not chunks:
            return {"error": "Could not create text chunks from PDF"}
        
        # Limit chunks for serverless processing
        chunks = chunks[:max_chunks]
        
        # Generate MCQs for each chunk
        all_mcqs = []
        for i, chunk in enumerate(chunks):
            print(f"Processing chunk {i + 1} of {len(chunks)}")
            mcqs = generate_mcqs_with_assistant(client, chunk)
            all_mcqs.extend(mcqs)
        
        if not all_mcqs:
            return {"error": "No MCQs could be generated from the PDF content"}
        
        # Deduplicate and return
        final_mcqs = deduplicate_mcqs(all_mcqs)
        
        return {
            "success": True,
            "mcqs": final_mcqs,
            "chunks_processed": len(chunks),
            "questions_generated": sum(len(block.get("questions", [])) for block in final_mcqs)
        }
        
    except Exception as e:
        return {"error": f"Failed to process PDF: {str(e)}"}

def validate_mcq_structure(mcq_data):
    """
    Validate MCQ data structure
    """
    if not isinstance(mcq_data, dict):
        return False, "MCQ data must be a dictionary"
    
    required_keys = ["topic", "questions"]
    for key in required_keys:
        if key not in mcq_data:
            return False, f"Missing required key: {key}"
    
    questions = mcq_data.get("questions", [])
    if not isinstance(questions, list):
        return False, "Questions must be a list"
    
    for i, question in enumerate(questions):
        if not isinstance(question, dict):
            return False, f"Question {i+1} must be a dictionary"
        
        question_required_keys = ["question", "options", "answer", "explanation"]
        for key in question_required_keys:
            if key not in question:
                return False, f"Question {i+1} missing required key: {key}"
        
        options = question.get("options", {})
        if not isinstance(options, dict):
            return False, f"Question {i+1} options must be a dictionary"
        
        expected_options = ["A", "B", "C", "D"]
        for opt in expected_options:
            if opt not in options:
                return False, f"Question {i+1} missing option {opt}"
    
    return True, "Valid MCQ structure"

# Utility functions for common operations
def count_words(text):
    """Count words in text"""
    if not text:
        return 0
    return len(text.split())

def truncate_text(text, max_words=1000):
    """Truncate text to maximum word count"""
    if not text:
        return ""
    
    words = text.split()
    if len(words) <= max_words:
        return text
    
    return " ".join(words[:max_words]) + "..."

def clean_text(text):
    """Basic text cleaning for better processing"""
    if not text:
        return ""
    
    # Remove excessive whitespace
    text = " ".join(text.split())
    
    # Remove non-printable characters
    text = "".join(char for char in text if char.isprintable() or char.isspace())
    
    return text.strip()
