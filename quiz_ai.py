import google.generativeai as genai
import json
import os
from PIL import Image
import io
import hashlib
from pathlib import Path
import time

# Load .env file into environment (simple safe loader, avoids extra deps)
def _load_local_env(path='.env'):
    p = Path(path)
    if not p.exists():
        return
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            # Only set if not already present in environment
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass

_load_local_env()

# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY", "your_api_key"))
MODEL_NAME = "gemini-2.5-flash"
MODEL = genai.GenerativeModel(MODEL_NAME)

quiz_sessions = {}

# Simple file-based cache to avoid repeated expensive model calls for the same input
CACHE_DIR = Path(".cache_quiz")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"

def _make_cache_key(text: str, question_count: int, difficulty: str) -> str:
    h = hashlib.sha256()
    h.update(text.encode("utf-8"))
    h.update(str(question_count).encode("utf-8"))
    h.update(difficulty.encode("utf-8"))
    return h.hexdigest()

def load_cache(key: str):
    p = _cache_path(key)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None

def save_cache(key: str, value):
    p = _cache_path(key)
    try:
        p.write_text(json.dumps(value))
    except Exception:
        pass

def compact_user_prompt(text: str, max_line_length: int = 220) -> str:
    """Remove excess whitespace and trim overly long lines before model calls.

    This keeps structured study notes compact without changing their meaning.
    """
    if not text:
        return ""

    compacted_lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line:
            continue
        if len(line) > max_line_length:
            line = line[: max_line_length - 3].rstrip() + "..."
        compacted_lines.append(line)

    return "\n".join(compacted_lines)

def summarize_text(text: str, question_count: int, chunk_size: int = 3000, max_summary_chars: int = 2000):
    """Hierarchical summarization: split long text into chunks, summarize each, then combine.
    This reduces the final prompt size while keeping key facts for quiz generation."""
    if not text or len(text) <= max_summary_chars:
        return text
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    summaries = []
    for c in chunks:
        prompt = (
            "Summarize the following notes into concise bullet points (2-4 sentences each)."
            " Keep factual details and key concepts useful for writing quiz questions. Return plain text only.\n\n"
            + c
        )
        try:
            resp = MODEL.generate_content(prompt)
            s = resp.text.strip() if resp and getattr(resp, 'text', None) else ""
            if s.startswith("```"):
                # remove code fence if present
                parts = s.split("```")
                if len(parts) >= 2:
                    s = parts[1].strip()
            summaries.append(s)
        except Exception:
            continue
    combined = "\n\n".join([s for s in summaries if s])
    if len(combined) > max_summary_chars:
        # final compress
        prompt2 = (
            f"Compress the following summaries into a single concise summary suitable for creating {question_count} quiz questions. Return plain text only.\n\n"
            + combined
        )
        try:
            resp2 = MODEL.generate_content(prompt2)
            combined = resp2.text.strip() if resp2 and getattr(resp2, 'text', None) else combined
            if combined.startswith("```"):
                combined = combined.replace("```", "").strip()
        except Exception:
            pass
    return combined

def start_quiz_session(session_id):
    """Start or reset a quiz session."""
    chat = MODEL.start_chat(history=[])
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

def extract_json_array(text: str):
    """Extract the first JSON array from a model response.

    This is intentionally forgiving because medium/hard generations sometimes
    wrap valid JSON in prose or code fences.
    """
    if not text:
        raise ValueError("Empty response text")

    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidate = cleaned[start:end + 1]
        return json.loads(candidate)

    return json.loads(cleaned)

def build_quiz_prompt(notes_text: str, question_count: int, difficulty: str) -> str:
    difficulty_instructions = {
        "easy": (
            "Create straightforward recall questions. Keep wording simple and the correct answer obvious from the notes."
        ),
        "medium": (
            "Create questions that test understanding and application. Focus on core ideas, cause-and-effect, comparisons, and short scenarios. "
            "Every question must be answerable directly from the notes without outside knowledge."
        ),
        "hard": (
            "Create challenging but still grounded questions. Ask about relationships between concepts, implications, and careful distinctions. "
            "Do not use trick questions, ambiguous wording, or outside knowledge."
        ),
    }

    return f"""Create EXACTLY {question_count} multiple choice questions based on these notes.
Return ONLY a valid JSON array and nothing else.

Difficulty level: {difficulty_instructions[difficulty]}

Rules:
- Each question object must have exactly these keys: question, options, correct_answer, explanation
- options must be an array of exactly 4 strings
- correct_answer must be an integer from 0 to 3
- explanation must briefly justify the correct answer using the notes
- Do not include markdown, code fences, numbering, or commentary

Example format:
[
  {{
    "question": "What is the capital of France?",
    "options": ["London", "Paris", "Berlin", "Madrid"],
    "correct_answer": 1,
    "explanation": "Paris is the capital city of France."
  }}
]

Notes to create questions from:
{notes_text}
"""

def repair_quiz_response(model, raw_text: str, question_count: int, difficulty: str):
    """Ask the model to repair malformed output into strict JSON."""
    repair_prompt = f"""Convert the following text into EXACTLY {question_count} valid quiz questions.
Return ONLY a JSON array, with each item having keys question, options, correct_answer, explanation.
Difficulty: {difficulty}

Text to repair:
{raw_text}
"""
    response = model.generate_content(repair_prompt)
    if not response or not response.text:
        raise ValueError("Empty repair response from model")
    return extract_json_array(response.text)

def generate_quiz_from_image(session_id, image_file, question_count=5, difficulty="medium"):
    """Generate multiple choice questions from an image."""
    try:
        # Read and process the image
        image = Image.open(image_file)
        # Use shared model instance
        model = MODEL
        
        prompt = build_quiz_prompt("[image content supplied separately]", question_count, difficulty)
        
        # Create cache key from image bytes + params
        try:
            img_bytes = io.BytesIO()
            image.save(img_bytes, format="PNG")
            img_hash = hashlib.sha256(img_bytes.getvalue()).hexdigest()
            cache_key = _make_cache_key(img_hash, question_count, difficulty)
            cached = load_cache(cache_key)
            if cached:
                return cached
        except Exception:
            cache_key = None

        response = model.generate_content([prompt, image])
        response_text = response.text.strip()

        # Parse the JSON, repairing malformed output once if needed
        try:
            questions = extract_json_array(response_text)
        except Exception:
            questions = repair_quiz_response(model, response_text, question_count, difficulty)
        if not isinstance(questions, list):
            questions = [questions]
            
        # Validate questions
        valid_questions = [q for q in questions if validate_question(q)]
        
        if valid_questions:
            if cache_key:
                save_cache(cache_key, valid_questions)
            return valid_questions
        else:
            raise ValueError("No valid questions found in response")
            
    except Exception as e:
        print(f"ERROR in generate_quiz_from_image: {str(e)}")
        import traceback
        traceback.print_exc()
        return [{
            "question": "Error generating questions from image. Please try again.",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_answer": 0,
            "explanation": "There was an error processing the image. Please try again with a different image or use text input instead."
        }]

def generate_quiz(session_id, user_prompt, question_count=5, difficulty="medium"):
    """Generate multiple choice questions from the provided notes."""
    try:
        # Use shared model instance
        model = MODEL
        normalized_prompt = compact_user_prompt(user_prompt)
        # Check cache first
        cache_key = _make_cache_key(normalized_prompt, question_count, difficulty)
        cached = load_cache(cache_key)
        if cached:
            return cached

        # If the input is moderately large, summarize it first to reduce token usage.
        notes_text = normalized_prompt
        if len(normalized_prompt) > 2200:
            notes_text = summarize_text(normalized_prompt, question_count)

        prompt = build_quiz_prompt(notes_text, question_count, difficulty)
        response = model.generate_content(prompt)
        if not response or not response.text:
            raise ValueError("Empty response from model")
        try:
            questions = extract_json_array(response.text)
        except Exception:
            questions = repair_quiz_response(model, response.text, question_count, difficulty)
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
            try:
                save_cache(cache_key, valid_questions)
            except Exception:
                pass
            return valid_questions
        else:
            raise ValueError("No valid questions found in response")
    except Exception as e:
        print(f"ERROR in generate_quiz: {str(e)}")
        import traceback
        traceback.print_exc()
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
