from flask import Flask, render_template, request, redirect, url_for, session
import os
import quiz_ai
from uuid import uuid4
import json
import socket
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Configure upload folder
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Create uploads folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_local_ip():
    try:
        # Get the local IP address
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except:
        return "127.0.0.1"

@app.route("/", methods=["GET", "POST"])
def index():
    if "session_id" not in session:
        session["session_id"] = str(uuid4())
    
    if request.method == "GET":
        last_response = session.get('last_response', [])
        input_type = session.get('input_type', 'text')
        return render_template("index.html", response=last_response, input_type=input_type)
    
    try:
        question_count = int(request.form.get("question_count", 5))
        difficulty = request.form.get("difficulty", "medium")
        
        if 'image' in request.files and request.files['image'].filename:
            session['input_type'] = 'image'
            image_file = request.files['image']
            if image_file and allowed_file(image_file.filename):
                filename = secure_filename(image_file.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                image_file.save(filepath)
                ai_response = quiz_ai.generate_quiz_from_image(session["session_id"], filepath, question_count, difficulty)
                os.remove(filepath)
                session['last_response'] = ai_response
                return redirect(url_for('index'))
            else:
                session['input_type'] = 'image'
                return render_template("index.html", error="Invalid image file. Please upload a valid image file.", response=[], input_type='image')
        else:
            session['input_type'] = 'text'
            prompt = request.form.get("prompt", "").strip()
            if not prompt:
                return render_template("index.html", error="Please enter some notes or upload an image.", response=[], input_type='text')
            try:
                ai_response = quiz_ai.generate_quiz(session["session_id"], prompt, question_count, difficulty)
                if isinstance(ai_response, str):
                    try:
                        ai_response = json.loads(ai_response)
                    except json.JSONDecodeError:
                        ai_response = [{
                            "question": ai_response,
                            "options": ["Option A", "Option B", "Option C", "Option D"],
                            "correct_answer": 0,
                            "explanation": "Please try generating the quiz again."
                        }]
                if not isinstance(ai_response, list):
                    ai_response = [ai_response]
                formatted_response = []
                for q in ai_response:
                    if isinstance(q, dict) and all(k in q for k in ["question", "options", "correct_answer", "explanation"]):
                        formatted_response.append({
                            "question": str(q["question"]),
                            "options": [str(opt) for opt in q["options"]],
                            "correct_answer": int(q["correct_answer"]),
                            "explanation": str(q["explanation"])
                        })
                if not formatted_response:
                    formatted_response = [{
                        "question": "Error generating questions. Please try again.",
                        "options": ["Option A", "Option B", "Option C", "Option D"],
                        "correct_answer": 0,
                        "explanation": "There was an error generating the quiz. Please try again with different notes."
                    }]
                session['last_response'] = formatted_response
                return redirect(url_for('index'))
            except Exception as e:
                return render_template("index.html", error=f"An error occurred while generating the quiz: {str(e)}", response=[], input_type='text')
    except Exception as e:
        return render_template("index.html", error=f"An error occurred: {str(e)}", response=[], input_type='text')

@app.route("/reset", methods=["POST"])
def reset():
    session.clear()
    session["session_id"] = str(uuid4())
    return redirect(url_for('index'))

if __name__ == "__main__":
    local_ip = get_local_ip()
    print("\n" + "="*50)
    print("Server is running! You can access the application at:")
    print(f"Local:   http://127.0.0.1:8080")
    print(f"Network: http://{local_ip}:8080")
    print("="*50 + "\n")
    app.run(host='0.0.0.0', port=8080, debug=True)  # Using port 8080 and allowing external access

