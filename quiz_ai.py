import google.generativeai as genai
import json
import os
from PIL import Image
import io

# Configure Gemini
genai.configure(api_key=os.getenv('GEMINI_API_KEY', "AIzaSyD-v_j4nxRo9vTTUMaAnMgLZyCeP6ku4qo"))
MODEL_NAME = "models/gemini-1.5-flash"

quiz_sessions = {}

def start_quiz_session(session_id):
    """Start or reset a quiz session."""
    model = genai.GenerativeModel(MODEL_NAME)
    chat = model.start_chat(history=[])
    quiz_sessions[session_id] = chat
    return chat

def get_quiz_session(session_id):
    """Retrieve or create a session."""
    return quiz_sessions.get(session_id) or start_quiz_session(session_id)

def validate_question(q):
    """Validate a single question object."""
    if not isinstance(q, dict) or not all(k in q for k in ["question", "options", "correct_answer", "explanation"]):
        return False
    if not isinstance(q["options"], list) or len(q["options"]) != 4:
        return False
    if not isinstance(q["correct_answer"], int) or not 0 <= q["correct_answer"] < 4:
        return False
    return True

def generate_quiz_from_image(session_id, image_file, question_count=5, difficulty="medium"):
    """Generate multiple choice questions from an image."""
    try:
        # Read and process the image
        image = Image.open(image_file)
        
        # Create a new model instance for image processing
        model = genai.GenerativeModel(MODEL_NAME)
        
        difficulty_instructions = {
            "easy": "Create straightforward questions that test basic understanding. Use simple language and focus on key facts.",
            "medium": "Create questions that test both understanding and application of concepts. Include some questions that require connecting ideas.",
            "hard": "Create challenging questions that test deep understanding. Include questions that require analysis, evaluation, or synthesis of concepts."
        }
        
        prompt = f"""Create EXACTLY {question_count} multiple choice questions based on the content in this image. Your response must be a valid JSON array.
        Difficulty level: {difficulty_instructions[difficulty]}
        
        Each question must be an object with these exact fields:
        - question: The question text
        - options: An array of exactly 4 possible answers
        - correct_answer: A number from 0 to 3 (index of correct answer)
        - explanation: Why the correct answer is right

        IMPORTANT: You MUST return EXACTLY {question_count} questions, no more and no less.
        If you cannot generate {question_count} questions, return an error message instead.

        Example response format (copy this exactly, just change the content):
        [
            {{
                "question": "What is the capital of France?",
                "options": ["London", "Paris", "Berlin", "Madrid"],
                "correct_answer": 1,
                "explanation": "Paris is the capital city of France."
            }}
        ]

        Remember: Return ONLY the JSON array with EXACTLY {question_count} questions, nothing else. No additional text."""
        
        response = model.generate_content([prompt, image])
        response_text = response.text.strip()
        
        # Clean the response text to ensure it's valid JSON
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        # Parse the JSON
        questions = json.loads(response_text)
        if not isinstance(questions, list):
            questions = [questions]
            
        # Validate questions
        valid_questions = [q for q in questions if validate_question(q)]
        
        if valid_questions:
            return valid_questions
        else:
            raise ValueError("No valid questions found in response")
            
    except Exception as e:
        return [{
            "question": "Error generating questions from image. Please try again.",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_answer": 0,
            "explanation": "There was an error processing the image. Please try again with a different image or use text input instead."
        }]

def generate_quiz(session_id, user_prompt, question_count=5, difficulty="medium"):
    """Generate multiple choice questions from the provided notes."""
    try:
        # Create a new model instance
        model = genai.GenerativeModel(MODEL_NAME)
        difficulty_instructions = {
            "easy": "Create straightforward questions that test basic understanding. Use simple language and focus on key facts.",
            "medium": "Create questions that test both understanding and application of concepts. Include some questions that require connecting ideas.",
            "hard": "Create challenging questions that test deep understanding. Include questions that require analysis, evaluation, or synthesis of concepts."
        }
        prompt = f"""Create EXACTLY {question_count} multiple choice questions based on these notes. Your response must be a valid JSON array.
        Difficulty level: {difficulty_instructions[difficulty]}
        
        Each question must be an object with these exact fields:
        - question: The question text
        - options: An array of exactly 4 possible answers
        - correct_answer: A number from 0 to 3 (index of correct answer)
        - explanation: Why the correct answer is right

        IMPORTANT: You MUST return EXACTLY {question_count} questions, no more and no less.
        If you cannot generate {question_count} questions, return an error message instead.

        Example response format (copy this exactly, just change the content):
        [
            {{
                "question": "What is the capital of France?",
                "options": ["London", "Paris", "Berlin", "Madrid"],
                "correct_answer": 1,
                "explanation": "Paris is the capital city of France."
            }}
        ]

        Notes to create questions from:
        {user_prompt}

        Remember: Return ONLY the JSON array with EXACTLY {question_count} questions, nothing else. No additional text."""
        response = model.generate_content(prompt)
        if not response or not response.text:
            raise ValueError("Empty response from model")
        response_text = response.text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        try:
            questions = json.loads(response_text)
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON response from model")
        if not isinstance(questions, list):
            questions = [questions]
        valid_questions = []
        for q in questions:
            if validate_question(q):
                valid_questions.append({
                    "question": str(q["question"]),
                    "options": [str(opt) for opt in q["options"]],
                    "correct_answer": int(q["correct_answer"]),
                    "explanation": str(q["explanation"])
                })
        if valid_questions:
            return valid_questions
        else:
            raise ValueError("No valid questions found in response")
    except Exception as e:
        return [{
            "question": "Error generating questions. Please try again.",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_answer": 0,
            "explanation": "There was an error generating the quiz. Please try again with different notes."
        }]

def reset_quiz_session(session_id):
    """Reset the quiz session."""
    start_quiz_session(session_id)

def get_quiz_history(session_id):
    """Return cleaned-up chat history."""
    chat = get_quiz_session(session_id)
    clean_history = []
    for msg in chat.history:
        parts = [str(p).replace("text: ", "").strip() for p in msg.parts]
        clean_history.append({"role": msg.role, "parts": parts})
    return clean_history
