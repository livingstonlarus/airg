from pathlib import Path
from typing import Dict, Tuple, Optional
from google import genai
from playwright.async_api import async_playwright
import logging
from datetime import datetime
import re

logger = logging.getLogger(__name__)

class DocumentGeneratorError(Exception):
    """Base exception for document generator errors"""
    pass

class DocumentGenerator:
    # Common prompt instructions
    _COMMON_INSTRUCTIONS = """
        IMPORTANT: 
        - Return ONLY the raw HTML content
        - Use EXACTLY the HTML structure provided
        - Do NOT modify ANY HTML tags or attributes
        - Do NOT wrap response in markdown code blocks
        - Start directly with <!DOCTYPE html>
    """

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("API key is required")
            
        self.client = genai.Client(api_key=api_key, http_options={'api_version': 'v1alpha'})
        self.base_dir = Path("resume_gen")
        self.base_dir.mkdir(exist_ok=True)
        self.resume_template = Path("resume/resume.html")
        self.letter_template = Path("resume/letter.html")
        
        # Validate template files exist
        if not self.resume_template.exists():
            raise FileNotFoundError(f"Resume template not found: {self.resume_template}")
        if not self.letter_template.exists():
            raise FileNotFoundError(f"Cover letter template not found: {self.letter_template}")

    def _sanitize_filename(self, text: str) -> str:
        """Create a safe filename from text"""
        return re.sub(r'[^\w\s-]', '', text).strip().replace(' ', '_')

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
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_job_title = self._sanitize_filename(job_title)
        dir_name = f"{timestamp}_{safe_job_title}"
        output_dir = self.base_dir / dir_name
        output_dir.mkdir(exist_ok=True)
        return output_dir

    def _get_file_paths(self, output_dir: Path, job_title: str) -> Dict[str, Path]:
        """Get all file paths for a job"""
        safe_job_title = self._sanitize_filename(job_title)
        return {
            'resume_html': output_dir / f"Resume_{safe_job_title}.html",
            'resume_pdf': output_dir / f"Resume_{safe_job_title}.pdf",
            'letter_html': output_dir / f"Cover_Letter_{safe_job_title}.html",
            'letter_pdf': output_dir / f"Cover_Letter_{safe_job_title}.pdf"
        }

    async def generate_documents(self, form_data: Dict[str, str]) -> Tuple[str, str]:
        """Generate customized resume and cover letter using Gemini API"""
        try:
            # Load resume template
            resume_template = self.resume_template.read_text()
            
            # Generate customized resume
            logger.info("Generating customized resume with Gemini...")
            customized_resume = await self._generate_content(
                self._create_resume_prompt(resume_template, form_data),
                "resume"
            )
            
            # Generate cover letter
            logger.info("Generating cover letter with Gemini...")
            cover_letter = await self._generate_content(
                self._create_cover_letter_prompt(customized_resume, form_data),
                "cover letter"
            )
            
            logger.info("Documents generated successfully")
            return customized_resume, cover_letter
            
        except Exception as e:
            logger.error(f"Error generating documents: {str(e)}")
            raise DocumentGeneratorError(f"Failed to generate documents: {str(e)}")

    async def _generate_content(self, prompt: str, doc_type: str) -> str:
        """Generate content using Gemini API"""
        try:
            response = self.client.models.generate_content(
                model='gemini-2.0-flash-thinking-exp',
                contents=prompt
            )
            
            if not response.candidates[0].content.parts:
                raise DocumentGeneratorError(f"Failed to generate {doc_type} content: No response from API")
            
            content = next(
                (part.text for part in response.candidates[0].content.parts 
                 if not getattr(part, 'thought', False)), 
                None
            )
            
            if not content:
                raise DocumentGeneratorError(f"Failed to generate {doc_type} content: Invalid response format")
                
            # Clean up the content before returning
            return self._cleanup_content(content)
            
        except Exception as e:
            raise DocumentGeneratorError(f"Error generating {doc_type}: {str(e)}")

    async def generate_pdfs(self, resume_html: str, cover_letter_html: str, job_title: str) -> Tuple[Path, Path]:
        """Generate PDFs from HTML content"""
        try:
            output_dir = self._create_output_dir(job_title)
            paths = self._get_file_paths(output_dir, job_title)
            
            # Save HTML files
            await self._save_html_files(
                paths['resume_html'], 
                paths['letter_html'], 
                resume_html, 
                cover_letter_html
            )
            
            # Generate PDFs
            await self._generate_pdf_files(paths)
            
            return paths['resume_pdf'], paths['letter_pdf']
            
        except Exception as e:
            logger.error(f"Error generating PDFs: {str(e)}")
            raise DocumentGeneratorError(f"Failed to generate PDFs: {str(e)}")

    async def _save_html_files(
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

    async def _generate_pdf_files(self, paths: Dict[str, Path]) -> None:
        """Generate PDF files from HTML files"""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                await page.set_viewport_size({"width": 1920, "height": 1080})
                
                async def generate_pdf(html_path: Path, pdf_path: Path):
                    if not html_path.exists():
                        raise FileNotFoundError(f"HTML file not found: {html_path}")
                        
                    file_url = f"file://{html_path.absolute()}"
                    await page.goto(file_url)
                    await page.wait_for_load_state('networkidle')
                    await page.wait_for_load_state('domcontentloaded')
                    await page.pdf(
                        path=str(pdf_path),
                        format="A4",
                        print_background=True,
                        margin={"top": "0", "right": "0", "bottom": "0", "left": "0"}
                    )
                
                logger.info("Generating PDF files...")
                await generate_pdf(paths['resume_html'], paths['resume_pdf'])
                await generate_pdf(paths['letter_html'], paths['letter_pdf'])
                await browser.close()
            
            if not paths['resume_pdf'].exists() or not paths['letter_pdf'].exists():
                raise DocumentGeneratorError("Failed to generate PDF files: Files not created")
            
        except Exception as e:
            raise DocumentGeneratorError(f"Failed to generate PDF files: {str(e)}")

    def _create_resume_prompt(self, resume_template: str, form_data: Dict[str, str]) -> str:
        """Create prompt for resume customization"""
        additional_context = ""
        if form_data.get('relevant_experience'):
            additional_context = f"""
            Additional Relevant Experience:
            {form_data['relevant_experience']}
            """
            
        return f"""
        Given this resume template:
        {resume_template}
        
        And this job description:
        {form_data['job_description']}
        
        Company Overview:
        {form_data['company_overview']}
        {additional_context}
        
        Instructions:
        1. Analyze the job description, company overview, and any additional relevant experience carefully
        2. IMPORTANT: Do NOT modify any HTML tags or structure - only update the text content within existing elements
        3. Customize ONLY the text content to better match the job requirements while keeping all HTML intact
        4. Highlight relevant skills and experience that match the job description
        5. If additional relevant experience was provided, incorporate it naturally into the appropriate sections
        6. Ensure the modifications are subtle and professional
        7. Keep all existing sections and their HTML structure exactly as is
        8. Add relevant keywords from the job description naturally within the existing text
        
        {self._COMMON_INSTRUCTIONS}
        """

    def _create_cover_letter_prompt(self, resume_content: str, form_data: Dict[str, str]) -> str:
        """Create prompt for cover letter generation"""
        salutation = "Mr." if form_data['hirer_gender'] == 'male' else "Ms."
        hirer_name = form_data.get('hirer_name', '')
        current_date = datetime.now().strftime("%B %d, %Y")
        
        additional_context = ""
        if form_data.get('relevant_experience'):
            additional_context = f"""
            Additional Context - Relevant Experience to Highlight:
            {form_data['relevant_experience']}
            """
            
        # Load the letter template from file
        letter_template = self.letter_template.read_text()
            
        return f"""
        Given this resume content:
        {resume_content}
        
        Create a professional cover letter with the following details:
        - Job Title: {form_data['job_title']}
        - Company: {form_data['company_name']}
        - Hiring Manager: {salutation} {hirer_name} if provided, otherwise omit the name
        - Date: {current_date}
        
        Job Description:
        {form_data['job_description']}
        
        Company Overview:
        {form_data['company_overview']}
        {additional_context}
        
        Instructions:
        1. IMPORTANT: Use EXACTLY this HTML structure - do not modify any tags:
        {letter_template}

        2. Only modify:
           - [DATE] with the provided date
           - [RECIPIENT] with the hiring manager details
           - The content within the letter-body div
        3. Focus on matching the candidate's experience with the job requirements
        4. If additional relevant experience was provided, emphasize it prominently in the letter
        5. Demonstrate understanding of the company's values and culture
        6. Keep the tone professional yet engaging
        7. Include a strong call to action in the closing paragraph
        8. Limit to 3-4 paragraphs
        
        {self._COMMON_INSTRUCTIONS}
        """ 