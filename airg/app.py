import os
import asyncio
import logging
from flask import Flask, render_template, flash, redirect, url_for, request, send_file, jsonify, send_from_directory, Response
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField
from wtforms.validators import DataRequired, Optional
from dotenv import load_dotenv
from pathlib import Path
import json
from .forms.job_details import JobDetailsForm
from .services.document_generator import DocumentGenerator

# Load environment variables first
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB max file size
# Only disable caching in debug mode
if os.getenv('FLASK_DEBUG', '0') == '1':
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# Initialize document generator with API key
doc_generator = DocumentGenerator(api_key=os.getenv('GEMINI_API_KEY'))

# Add route to serve static files from resume directory
@app.route('/resume/<path:filename>')
def serve_resume_file(filename):
    return send_from_directory('resume', filename)

# Add route to serve generated files from resume_gen directory
@app.route('/resume_gen/<path:filename>')
def serve_generated_file(filename):
    # Get the absolute path to the resume_gen directory
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'resume_gen'))
    
    # Get the directory part and the file name
    directory = os.path.dirname(filename)
    file = os.path.basename(filename)
    
    # If there's a directory part, use it as the subdirectory
    if directory:
        full_dir = os.path.join(base_dir, directory)
        return send_from_directory(full_dir, file)
    return send_from_directory(base_dir, file)

def validate_resume_file(file_path: Path) -> bool:
    """Validate the resume HTML file"""
    try:
        content = file_path.read_text()
        if not content.strip():
            raise ValueError("Resume file is empty")
        if not content.lower().startswith('<!doctype html') and not content.lower().startswith('<html'):
            raise ValueError("File does not appear to be valid HTML")
        return True
    except Exception as e:
        logger.error(f"Resume file validation error: {str(e)}")
        return False

@app.route('/stream')
async def stream():
    """Stream updates to the client"""
    def generate():
        while True:
            if not app.config.get('message_queue', []):
                yield 'data: {"type": "ping"}\n\n'
            else:
                message = app.config['message_queue'].pop(0)
                yield f'data: {json.dumps(message)}\n\n'
            yield 'data: {"type": "ping"}\n\n'
    
    return Response(generate(), mimetype='text/event-stream')

def send_update(message: str, category: str = 'info'):
    """Send an update to the client"""
    if not hasattr(app.config, 'message_queue'):
        app.config['message_queue'] = []
    app.config['message_queue'].append({
        'message': message,
        'category': category
    })

@app.route('/', methods=['GET', 'POST'])
async def index():
    if request.method == 'POST':
        try:
            form = JobDetailsForm(request.form)
            if not form.validate():
                return jsonify({'error': 'Please fill out all required fields correctly.'}), 400

            # Get form data
            form_data = {
                'job_title': form.job_title.data,
                'company_name': form.company_name.data,
                'hirer_name': form.hirer_name.data or '',
                'hirer_gender': form.hirer_gender.data or '',
                'job_description': form.job_description.data,
                'company_overview': form.company_overview.data,
                'relevant_experience': form.relevant_experience.data or ''
            }

            # Read resume content
            resume_path = Path('resume/resume.html')
            if not resume_path.exists():
                return jsonify({'error': 'Resume template not found.'}), 404
            
            resume_content = resume_path.read_text()
            
            # Initialize document generator
            doc_gen = DocumentGenerator(api_key=os.getenv('GEMINI_API_KEY'))
            
            # Send initial status
            send_update('Starting document generation process...', 'info')
            
            # Generate documents
            try:
                customized_resume, cover_letter = await doc_gen.generate_documents(resume_content, form_data)
                send_update('Documents generated successfully!', 'success')
            except Exception as e:
                logger.error(f"Error generating documents: {str(e)}")
                return jsonify({'error': 'Failed to generate documents. Please try again.'}), 500

            # Generate PDFs
            try:
                send_update('Converting documents to PDF format...', 'info')
                resume_pdf_path, cover_letter_pdf_path = await doc_gen.generate_pdfs(
                    customized_resume, 
                    cover_letter,
                    form_data['job_title']
                )
                send_update('Documents ready for download!', 'success')
                
                # Return file paths for download
                return jsonify({
                    'success': True,
                    'directory': resume_pdf_path.parent.name,
                    'resume_path': resume_pdf_path.name,
                    'cover_letter_path': cover_letter_pdf_path.name
                })
                
            except Exception as e:
                logger.error(f"Error generating PDFs: {str(e)}")
                return jsonify({'error': 'Failed to generate PDF files. Please try again.'}), 500

        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500

    return render_template('index.html', form=JobDetailsForm())

@app.route('/download/<path:filename>')
def download_file(filename):
    try:
        # Ensure the path is relative to resume_gen directory
        file_path = Path('resume_gen') / filename
        if not file_path.exists():
            raise FileNotFoundError(f"File {filename} not found")
        
        return send_file(
            file_path,
            as_attachment=True,
            download_name=file_path.name
        )
        
    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
        flash('Error downloading file. Please try again.', 'error')
        return redirect(url_for('index'))

@app.errorhandler(413)
def request_entity_too_large(error):
    """Handle file size exceeded error"""
    flash('File size exceeded the maximum limit (10MB)', 'error')
    return render_template('index.html', form=JobDetailsForm()), 413

@app.errorhandler(500)
def internal_server_error(error):
    """Handle internal server errors"""
    logger.error(f"Internal server error: {str(error)}")
    flash('An internal server error occurred. Please try again later.', 'error')
    return render_template('index.html', form=JobDetailsForm()), 500

if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', '0') == '1'
    app.run(host='localhost', port=5000, debug=debug_mode) 