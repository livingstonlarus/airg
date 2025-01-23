import os
import logging
from datetime import datetime
from flask import Flask, render_template, jsonify, send_from_directory, Response, request, abort
from dotenv import load_dotenv
from pathlib import Path
import json
import time
from .forms.job_details import JobDetailsForm
from .services.document_generator import DocumentGenerator, DocumentGeneratorError

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
app.config['MESSAGE_QUEUE_MAX_SIZE'] = 100  # Maximum number of messages in queue
app.config['STREAM_TIMEOUT'] = 300  # 5 minutes timeout for streams
# Only disable caching in debug mode
if os.getenv('FLASK_DEBUG', '0') == '1':
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# Initialize document generator with API key
api_key = os.getenv('GEMINI_API_KEY')
if not api_key:
    raise ValueError("GEMINI_API_KEY environment variable is required")
    
doc_generator = DocumentGenerator(api_key=api_key)

def validate_static_path(directory: str, filename: str) -> bool:
    """Validate static file path to prevent directory traversal"""
    try:
        if directory not in ['resume', 'resume_gen']:
            return False
            
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', directory))
        requested_path = os.path.abspath(os.path.join(base_dir, filename))
        
        # Check if the requested path is within the allowed directory
        return requested_path.startswith(base_dir)
        
    except Exception:
        return False

# Add route to serve static files from resume and resume_gen directories
@app.route('/<path:directory>/<path:filename>')
def serve_static_file(directory: str, filename: str):
    """Serve static files from resume and resume_gen directories"""
    if not validate_static_path(directory, filename):
        abort(404)
    
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', directory))
    
    # If there's a subdirectory in the filename
    subdir = os.path.dirname(filename)
    if subdir:
        full_dir = os.path.join(base_dir, subdir)
        if not validate_static_path(directory, os.path.join(subdir, os.path.basename(filename))):
            abort(404)
        return send_from_directory(full_dir, os.path.basename(filename))
        
    return send_from_directory(base_dir, filename)

@app.route('/stream')
async def stream():
    """Stream updates to the client"""
    def generate():
        start_time = time.time()
        while True:
            # Check timeout
            if time.time() - start_time > app.config['STREAM_TIMEOUT']:
                logger.warning("Stream timeout reached")
                break
                
            if not app.config.get('message_queue', []):
                yield 'data: {"type": "ping"}\n\n'
            else:
                message = app.config['message_queue'].pop(0)
                yield f'data: {json.dumps(message)}\n\n'
            yield 'data: {"type": "ping"}\n\n'
            time.sleep(0.1)  # Prevent tight loop
    
    return Response(generate(), mimetype='text/event-stream')

def send_update(message: str, category: str = 'info'):
    """Send an update to the client"""
    if not hasattr(app.config, 'message_queue'):
        app.config['message_queue'] = []
    # Limit queue size
    if len(app.config['message_queue']) >= app.config['MESSAGE_QUEUE_MAX_SIZE']:
        app.config['message_queue'].pop(0)  # Remove oldest message
    app.config['message_queue'].append({
        'message': message,
        'category': category,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/', methods=['GET', 'POST'])
async def index():
    if request.method == 'POST':
        try:
            form = JobDetailsForm(request.form)
            if not form.validate():
                errors = []
                for field, field_errors in form.errors.items():
                    for error in field_errors:
                        errors.append(f"{field}: {error}")
                return jsonify({'error': 'Validation failed', 'details': errors}), 400

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
            
            # Send initial status
            send_update('Starting document generation process...', 'info')
            
            # Generate documents
            try:
                customized_resume, cover_letter = await doc_generator.generate_documents(form_data)
                send_update('Documents generated successfully!', 'success')
            except DocumentGeneratorError as e:
                logger.error(f"Error generating documents: {str(e)}")
                return jsonify({'error': str(e)}), 500
            except Exception as e:
                logger.error(f"Unexpected error generating documents: {str(e)}")
                return jsonify({'error': 'An unexpected error occurred while generating documents.'}), 500

            # Generate PDFs
            try:
                send_update('Converting documents to PDF format...', 'info')
                resume_pdf_path, cover_letter_pdf_path = await doc_generator.generate_pdfs(
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
                
            except DocumentGeneratorError as e:
                logger.error(f"Error generating PDFs: {str(e)}")
                return jsonify({'error': str(e)}), 500
            except Exception as e:
                logger.error(f"Unexpected error generating PDFs: {str(e)}")
                return jsonify({'error': 'An unexpected error occurred while generating PDFs.'}), 500

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