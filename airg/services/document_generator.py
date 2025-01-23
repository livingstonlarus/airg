from pathlib import Path
from typing import Dict, Tuple, Optional, TypedDict
import google.generativeai as genai
from playwright.sync_api import sync_playwright
import logging
from datetime import datetime
import re
from unidecode import unidecode
import json

logger = logging.getLogger(__name__)

class DocumentMetadata(TypedDict):
    applicant_name: str
    company_name_short: str
    job_title_short: str
    resume_title_short: str

class DocumentGeneratorError(Exception):
    """Base exception for document generator errors"""
    pass

class DocumentGenerator:
    # Common prompt instructions
    _COMMON_INSTRUCTIONS = """
        IMPORTANT: 
        - Return ONLY valid JSON
        - Follow the exact structure requested
        - Ensure all text values are properly sanitized
        - Keep shortened versions meaningful and professional
    """
    
    _REQUIRED_JSON_FIELDS = {
        'resume_html', 'cover_letter_html', 
        'applicant_name', 'company_name_short',
        'job_title_short', 'resume_title_short'
    }

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("API key is required")
            
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(
            model_name='gemini-2.0-flash-thinking-exp',
            generation_config={'temperature': 0.1}
        )
        self.base_dir = Path("resume_gen")
        self.base_dir.mkdir(exist_ok=True)
        self.resume_template = Path("resume/resume.html")
        self.letter_template = Path("resume/letter.html")
        
        # Initialize metadata
        self.document_metadata: Optional[DocumentMetadata] = None
        
        # Validate template files exist
        if not self.resume_template.exists():
            raise FileNotFoundError(f"Resume template not found: {self.resume_template}")
        if not self.letter_template.exists():
            raise FileNotFoundError(f"Cover letter template not found: {self.letter_template}")

    def _sanitize_filename(self, text: str) -> str:
        """Create a safe filename from text"""
        # Convert to ASCII, remove diacritics
        text = unidecode(text)
        # Replace any non-alphanumeric with underscore
        text = re.sub(r'[^\w]', '_', text)
        # Replace multiple underscores with single
        text = re.sub(r'_+', '_', text)
        # Strip leading/trailing underscores and convert to lowercase
        return text.strip('_').lower()

    def _format_timestamp(self) -> str:
        """Create a compact timestamp YYMMDDHHMMSS"""
        return datetime.now().strftime("%y%m%d%H%M%S")

    def _cleanup_content(self, content: str) -> str:
        """Clean up the content returned by the AI"""
        # Remove markdown code block markers if present
        content = re.sub(r'^```html\s*\n', '', content)
        content = re.sub(r'\n```\s*$', '', content)
        # Remove any html comments that might be present
        content = re.sub(r'<!--.*?-->\n?', '', content, flags=re.DOTALL)
        return content.strip()

    def _create_output_dir(self, job_title: str) -> Path:
        """Create a timestamped output directory for this generation"""
        if not self.document_metadata:
            raise DocumentGeneratorError("Document metadata not initialized")
            
        timestamp = self._format_timestamp()
        company = self._sanitize_filename(self.document_metadata['company_name_short'])
        job = self._sanitize_filename(self.document_metadata['job_title_short'])
        dir_name = f"{timestamp}_{company}_{job}"
        
        if not self._validate_filename(dir_name):
            raise DocumentGeneratorError(
                f"Generated directory name does not meet requirements: {dir_name}"
            )
            
        output_dir = self.base_dir / dir_name
        output_dir.mkdir(exist_ok=True)
        return output_dir

    def _get_file_paths(self, output_dir: Path, job_title: str) -> Dict[str, Path]:
        """Get all file paths for a job"""
        if not self.document_metadata:
            raise DocumentGeneratorError("Document metadata not initialized")
            
        # Use stored metadata for file naming
        applicant = self._sanitize_filename(self.document_metadata['applicant_name'])
        job_title = self._sanitize_filename(self.document_metadata['job_title_short'])
        resume_title = self._sanitize_filename(self.document_metadata['resume_title_short'])
        
        # Ensure each component is valid
        for component in [applicant, job_title, resume_title]:
            if not self._validate_filename(component):
                raise DocumentGeneratorError(f"Invalid filename component: {component}")
        
        return {
            'resume_html': output_dir / f"{applicant}_{resume_title}.html",
            'resume_pdf': output_dir / f"{applicant}_{resume_title}.pdf",
            'letter_html': output_dir / f"{applicant}_cover_letter_{job_title}.html",
            'letter_pdf': output_dir / f"{applicant}_cover_letter_{job_title}.pdf"
        }

    def _generate_content(self, prompt: str) -> str:
        """Generate content using the AI API"""
        try:
            logger.info("Making API call to AI service...")
            response = self.model.generate_content(prompt)
            logger.info("Received response from AI service")
            
            if not response.text:
                logger.error("No response text from API")
                raise DocumentGeneratorError("No response from API")
            
            # Log raw response for debugging
            logger.debug(f"Raw response: {response.text[:500]}...")
            
            # Try to extract JSON from the response
            text = response.text.strip()
            # Remove any markdown code block markers
            text = re.sub(r'^```json\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            # Remove any explanatory text before or after the JSON
            text = re.sub(r'^[^{]*({.*})[^}]*$', r'\1', text, flags=re.DOTALL)
            
            logger.info("Successfully cleaned response")
            logger.debug(f"Cleaned response: {text[:500]}...")
            
            return text
            
        except Exception as e:
            logger.error(f"Error in _generate_content: {str(e)}", exc_info=True)
            raise DocumentGeneratorError(f"Error generating content: {str(e)}")

    def generate_documents(self, form_data: Dict[str, str]) -> Tuple[str, str]:
        """Generate customized resume and cover letter using AI"""
        try:
            # Load templates
            logger.info("Loading templates...")
            resume_template = self.resume_template.read_text()
            letter_template = self.letter_template.read_text()
            logger.info("Templates loaded successfully")
            
            # Generate content with a single API call
            logger.info("Creating unified prompt...")
            prompt = self._create_unified_prompt(resume_template, letter_template, form_data)
            logger.debug(f"Prompt length: {len(prompt)} characters")
            
            logger.info("Generating documents with AI...")
            response = self._generate_content(prompt)
            
            # Parse and validate the JSON response
            try:
                logger.info("Parsing JSON response...")
                content = json.loads(response)
                
                logger.info("Validating JSON structure...")
                self._validate_json_response(content)
                
                # Store metadata for file naming
                logger.info("Storing document metadata...")
                self.document_metadata = {
                    'applicant_name': content['applicant_name'],
                    'company_name_short': content['company_name_short'],
                    'job_title_short': content['job_title_short'],
                    'resume_title_short': content['resume_title_short']
                }
                logger.debug(f"Document metadata: {self.document_metadata}")
                
                # Validate all generated filenames
                logger.info("Validating generated filenames...")
                output_dir = Path("test")  # Temporary path for validation
                paths = self._get_file_paths(output_dir, "test")
                for path in paths.values():
                    if not self._validate_filename(path.name):
                        logger.error(f"Invalid filename generated: {path.name}")
                        raise DocumentGeneratorError(
                            f"Generated filename does not meet requirements: {path.name}"
                        )
                
                logger.info("HTML content generated successfully")
                return content['resume_html'], content['cover_letter_html']
                
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {str(e)}", exc_info=True)
                raise DocumentGeneratorError(f"Invalid JSON response from API: {str(e)}")
            
        except Exception as e:
            logger.error(f"Error generating documents: {str(e)}", exc_info=True)
            raise DocumentGeneratorError(f"Failed to generate documents: {str(e)}")

    def _create_unified_prompt(self, resume_template: str, letter_template: str, form_data: Dict[str, str]) -> str:
        """Create unified prompt for document generation"""
        current_date = datetime.now().strftime("%B %d, %Y")
        salutation = "Mr." if form_data.get('hirer_gender') == 'male' else "Ms."
        hirer_name = form_data.get('hirer_name', '')
        
        additional_context = ""
        if form_data.get('relevant_experience'):
            additional_context = f"""
            Additional Context - Relevant Experience to Highlight:
            {form_data['relevant_experience']}
            """
            
        prompt = f"""
        You are a professional resume and cover letter generator. Your task is to generate both documents and return them in a specific JSON format.

        IMPORTANT INSTRUCTIONS:
        1. Your response must be ONLY valid JSON, nothing else
        2. Do not include any explanations or markdown formatting
        3. The JSON must follow this exact structure:
        {{
            "resume_html": "<complete HTML content>",
            "cover_letter_html": "<complete HTML content>",
            "applicant_name": "<full_name>",
            "company_name_short": "<short_company_name>",
            "job_title_short": "<short_job_title>",
            "resume_title_short": "<resume_type>"
        }}

        FILENAME REQUIREMENTS:
        - All short names must be at least 5 characters long
        - Use only lowercase letters, numbers, and underscores
        - No spaces, dots, or special characters
        - No consecutive underscores
        - For company_name_short:
          * NEVER add words, prefixes, or suffixes (like 'co', 'inc', 'tech', etc.)
          * ONLY remove words from the original name (like 'Ltd', 'Inc', 'Group', 'Technologies', etc.)
          * If the shortened name would be too short, keep more of the original name
          * Examples:
            - "Lazer" -> "lazer" (add underscores if needed: "lazer_group")
            - "Shopify Inc." -> "shopify"
            - "Microsoft Corporation" -> "microsoft"
        - Examples for other fields:
          - job_title_short: "delivery_director" (from "Director of Delivery")
          - resume_title_short: "senior_resume" (from "Senior Professional Resume")
          - applicant_name: "john_doe" (from full name)

        Use these templates and information:

        Resume Template:
        {resume_template}

        Cover Letter Template:
        {letter_template}

        Job Details:
        - Title: {form_data.get('job_title', '')}
        - Company: {form_data.get('company_name', '')}
        - Hiring Manager: {salutation} {hirer_name} if provided
        {additional_context}

        Current Date: {current_date}

        {self._COMMON_INSTRUCTIONS}
        """
        return prompt

    def generate_pdfs(self, resume_html: str, cover_letter_html: str, job_title: str) -> Tuple[Path, Path]:
        """Generate PDFs from HTML content"""
        try:
            logger.info("Creating output directory...")
            output_dir = self._create_output_dir(job_title)
            paths = self._get_file_paths(output_dir, job_title)
            logger.debug(f"Output paths: {paths}")
            
            # Save HTML files
            logger.info("Saving HTML files...")
            self._save_html_files(
                paths['resume_html'], 
                paths['letter_html'], 
                resume_html, 
                cover_letter_html
            )
            logger.info("HTML files saved successfully")
            
            # Generate PDFs
            logger.info("Starting PDF generation...")
            self._generate_pdf_files(paths)
            logger.info("PDF files generated successfully")
            
            return paths['resume_pdf'], paths['letter_pdf']
            
        except Exception as e:
            logger.error(f"Error generating PDFs: {str(e)}", exc_info=True)
            raise DocumentGeneratorError(f"Failed to generate PDFs: {str(e)}")

    def _save_html_files(
        self, resume_path: Path, letter_path: Path, 
        resume_html: str, cover_letter_html: str
    ) -> None:
        """Save HTML content to files"""
        try:
            # Read CSS content
            css_path = Path('resume/style.css')
            if not css_path.exists():
                raise FileNotFoundError("CSS file not found")
                
            css_content = css_path.read_text()
            
            def prepare_html(html_content: str) -> str:
                html_content = html_content.replace('<link rel="stylesheet" href="/resume/style.css">', '')
                css_tag = f'<style>{css_content}</style>'
                return html_content.replace('</head>', f'{css_tag}</head>')
            
            # Save HTML files with embedded CSS
            resume_path.write_text(prepare_html(resume_html))
            letter_path.write_text(prepare_html(cover_letter_html))
            
        except Exception as e:
            raise DocumentGeneratorError(f"Failed to save HTML files: {str(e)}")

    def _generate_pdf_files(self, paths: Dict[str, Path]) -> None:
        """Generate PDF files from HTML files"""
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.set_viewport_size({"width": 1920, "height": 1080})
                
                def generate_pdf(html_path: Path, pdf_path: Path):
                    if not html_path.exists():
                        raise FileNotFoundError(f"HTML file not found: {html_path}")
                        
                    file_url = f"file://{html_path.absolute()}"
                    page.goto(file_url)
                    page.wait_for_load_state('networkidle')
                    page.wait_for_load_state('domcontentloaded')
                    page.pdf(
                        path=str(pdf_path),
                        format="A4",
                        print_background=True,
                        margin={"top": "0", "right": "0", "bottom": "0", "left": "0"}
                    )
                
                logger.info("Generating PDF files...")
                generate_pdf(paths['resume_html'], paths['resume_pdf'])
                generate_pdf(paths['letter_html'], paths['letter_pdf'])
                browser.close()
            
            if not paths['resume_pdf'].exists() or not paths['letter_pdf'].exists():
                raise DocumentGeneratorError("Failed to generate PDF files: Files not created")
            
        except Exception as e:
            raise DocumentGeneratorError(f"Failed to generate PDF files: {str(e)}")

    def _validate_json_response(self, content: Dict) -> None:
        """Validate the JSON response from AI API"""
        missing_fields = self._REQUIRED_JSON_FIELDS - set(content.keys())
        if missing_fields:
            raise DocumentGeneratorError(
                f"Invalid JSON response: Missing required fields: {missing_fields}"
            )
        
        # Validate that no field is empty
        empty_fields = [
            field for field in self._REQUIRED_JSON_FIELDS 
            if not content.get(field)
        ]
        if empty_fields:
            raise DocumentGeneratorError(
                f"Invalid JSON response: Empty values for fields: {empty_fields}"
            )

    def _validate_filename(self, filename: str) -> bool:
        """Validate that a filename meets our requirements"""
        # Must be at least 5 characters long
        if len(filename) < 5:
            return False
        
        # Must only contain lowercase letters, numbers, underscores, and dots
        if not re.match(r'^[a-z0-9_\.]+$', filename):
            return False
            
        # Must not start or end with a dot or underscore
        if filename[0] in '._' or filename[-1] in '._':
            return False
            
        # Must not contain consecutive dots or underscores
        if '..' in filename or '__' in filename:
            return False
            
        return True 