from gevent import monkey
monkey.patch_all()

from gevent.pywsgi import WSGIServer
import os
import logging
from datetime import datetime
from flask import Flask, render_template, jsonify, send_from_directory, Response, request, abort, flash, redirect, url_for, send_file
from dotenv import load_dotenv
from pathlib import Path
import json
import time
import queue
from flask_wtf import FlaskForm
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
app.config['WTF_CSRF_ENABLED'] = True  # Enable CSRF protection

# Create a thread-safe message queue
message_queue = queue.Queue(maxsize=app.config['MESSAGE_QUEUE_MAX_SIZE'])

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
def stream():
    def generate():
        try:
            # Send initial connection message
            yield 'data: {"type": "connected"}\n\n'
            
            # Keep track of last ping time
            last_ping = time.time()
            PING_INTERVAL = 5  # Send ping every 5 seconds
            
            while True:
                current_time = time.time()
                
                try:
                    # Try to get a message with a short timeout
                    try:
                        msg = message_queue.get(timeout=0.1)
                        if msg:
                            yield f'data: {json.dumps(msg)}\n\n'
                    except queue.Empty:
                        # If it's time to send a ping
                        if current_time - last_ping >= PING_INTERVAL:
                            yield 'data: {"type": "ping"}\n\n'
                            last_ping = current_time
                    
                except Exception as e:
                    logger.error(f"Error processing message: {str(e)}")
                    continue
                
                # Use gevent sleep for a short duration
                from gevent import sleep
                sleep(0.1)
                
        except GeneratorExit:
            logger.info("Client disconnected from SSE stream")
        except Exception as e:
            logger.error(f"Error in SSE stream: {str(e)}")
            raise
    
    logger.info("New client connected to SSE stream")
    
    response = Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )
    return response

def send_update(message: str, category: str = 'info'):
    """Send an update to the client"""
    try:
        logger.info(f"Attempting to send update: {message} ({category})")
        # Try to add message to queue, non-blocking
        update = {
            'message': message,
            'category': category,
            'timestamp': datetime.now().isoformat()
        }
        message_queue.put_nowait(update)
        logger.info(f"Successfully queued update: {update}")
    except queue.Full:
        logger.warning("Message queue is full, attempting to clear space")
        # If queue is full, remove oldest message and try again
        try:
            message_queue.get_nowait()
            message_queue.put_nowait(update)
            logger.info("Successfully queued update after clearing space")
        except (queue.Empty, queue.Full):
            logger.error("Failed to send update: Queue is full and could not be cleared")

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        try:
            logger.info("Form submission received")
            send_update("Form submission received, starting processing...", "info")
            
            form = JobDetailsForm(request.form)
            if not form.validate():
                logger.error("Form validation failed")
                errors = []
                for field, field_errors in form.errors.items():
                    for error in field_errors:
                        errors.append(f"{field}: {error}")
                return jsonify({
                    'error': 'Form validation failed. Please check your inputs.',
                    'details': errors
                }), 400

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
            logger.info(f"Form data received: {form_data['job_title']} at {form_data['company_name']}")
            send_update(f"Sending data to AI for processing...", "info")
            
            # Clear message queue before starting new generation
            while not message_queue.empty():
                try:
                    message_queue.get_nowait()
                except queue.Empty:
                    break
            
            # Generate documents
            try:
                send_update("Starting document generation...", "info")
                resume_html, cover_letter_html = doc_generator.generate_documents(form_data)
                send_update("AI response received", "info")
                
                # Generate PDFs
                send_update("Generating PDF files...", "info")
                resume_pdf_path, cover_letter_pdf_path = doc_generator.generate_pdfs(
                    resume_html, cover_letter_html, form_data['job_title']
                )
                send_update("PDF files generated successfully!", "success")
                
                # Return file paths for download
                return jsonify({
                    'resume_pdf': resume_pdf_path.name,
                    'cover_letter_pdf': cover_letter_pdf_path.name,
                    'directory': resume_pdf_path.parent.name,
                    'message': 'Documents ready for download'
                })

            except DocumentGeneratorError as e:
                logger.error(f"Document generation error: {str(e)}")
                send_update(f"Error: {str(e)}", "error")
                return jsonify({'error': str(e)}), 500
                
            except Exception as e:
                logger.error(f"Unexpected error in form handling: {str(e)}", exc_info=True)
                send_update("An unexpected error occurred. Please try again.", "error")
                return jsonify({'error': 'Internal server error'}), 500

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Unexpected error in form handling: {error_msg}", exc_info=True)
            send_update(f"An unexpected error occurred. Please try again.", "error")
            return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500

    form = JobDetailsForm()
    return render_template('index.html', form=form)

@app.route('/download/<path:filename>')
def download_file(filename):
    try:
        # Get absolute path to resume_gen directory
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'resume_gen'))
        file_path = os.path.join(base_dir, filename)
        
        # Validate the path is within resume_gen directory
        abs_file_path = os.path.abspath(file_path)
        if not abs_file_path.startswith(base_dir):
            logger.error(f"Invalid file path: {filename}")
            abort(404)
            
        # Check if file exists
        if not os.path.exists(abs_file_path):
            logger.error(f"File not found: {filename}")
            abort(404)
            
        # Get the filename from the path
        download_name = os.path.basename(filename)
        
        # Send the file
        return send_file(
            abs_file_path,
            as_attachment=True,
            download_name=download_name,
            mimetype='application/pdf'
        )
        
    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
        abort(500)

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

@app.route('/favicon.ico')
def favicon():
    """Serve the favicon"""
    return send_from_directory(
        os.path.join(app.root_path, 'static'),
        'favicon.png',
        mimetype='image/png'
    )

if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', '0') == '1'
    if debug_mode:
        app.run(
            host='localhost',
            port=int(os.getenv('PORT', 5000)),
            debug=True
        )
    else:
        http_server = WSGIServer(('localhost', int(os.getenv('PORT', 5000))), app)
        http_server.serve_forever() 