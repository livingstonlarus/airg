from pathlib import Path
from typing import Dict, Tuple
from google import genai
from google.genai import types
from playwright.async_api import async_playwright
import logging
from flask import flash
from datetime import datetime
import re

logger = logging.getLogger(__name__)

class DocumentGenerator:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key, http_options={'api_version': 'v1alpha'})
        self.config = {'thinking_config': {'include_thoughts': True}}
        self.base_dir = Path("resume_gen")
        self.base_dir.mkdir(exist_ok=True)
        self.resume_template = Path("resume/resume.html")
        self.letter_template = Path("resume/letter.html")

    def _create_output_dir(self, job_title: str) -> Path:
        """Create a timestamped output directory for this generation"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_job_title = re.sub(r'[^\w\s-]', '', job_title).strip().replace(' ', '_')
        dir_name = f"{timestamp}_{safe_job_title}"
        output_dir = self.base_dir / dir_name
        output_dir.mkdir(exist_ok=True)
        return output_dir

    async def generate_documents(self, resume_content: str, form_data: Dict[str, str]) -> Tuple[str, str]:
        """Generate customized resume and cover letter using Gemini API"""
        try:
            # Generate customized resume
            logger.info("Generating customized resume with Gemini...")
            resume_prompt = self._create_resume_prompt(resume_content, form_data)
            resume_response = self.client.models.generate_content(
                model='gemini-2.0-flash-thinking-exp',
                contents=resume_prompt
            )
            
            if not resume_response.candidates[0].content.parts:
                raise ValueError("Failed to generate resume content")
            
            # Get the non-thought part of the response
            customized_resume = next(
                (part.text for part in resume_response.candidates[0].content.parts 
                 if not getattr(part, 'thought', False)), 
                None
            )
            
            if not customized_resume:
                raise ValueError("No valid resume content in response")
            
            # Generate cover letter
            logger.info("Generating cover letter with Gemini...")
            cover_letter_prompt = self._create_cover_letter_prompt(resume_content, form_data)
            cover_letter_response = self.client.models.generate_content(
                model='gemini-2.0-flash-thinking-exp',
                contents=cover_letter_prompt
            )
            
            if not cover_letter_response.candidates[0].content.parts:
                raise ValueError("Failed to generate cover letter content")
            
            # Get the non-thought part of the response
            cover_letter = next(
                (part.text for part in cover_letter_response.candidates[0].content.parts 
                 if not getattr(part, 'thought', False)), 
                None
            )
            
            if not cover_letter:
                raise ValueError("No valid cover letter content in response")
            
            logger.info("Documents generated successfully")
            return customized_resume, cover_letter
            
        except Exception as e:
            logger.error(f"Error generating documents: {str(e)}")
            raise

    async def generate_pdfs(self, resume_html: str, cover_letter_html: str, job_title: str) -> Tuple[Path, Path]:
        """Generate PDFs using Playwright"""
        try:
            logger.info("Starting PDF generation...")
            
            # Create output directory
            output_dir = self._create_output_dir(job_title)
            
            # Create base filenames
            safe_job_title = re.sub(r'[^\w\s-]', '', job_title).strip().replace(' ', '_')
            base_resume_name = f"Resume_{safe_job_title}"
            base_cover_letter_name = f"Cover_Letter_{safe_job_title}"
            
            # Read CSS content
            css_path = Path('resume/style.css')
            css_content = css_path.read_text()
            
            # Save HTML files with embedded CSS
            def prepare_html(html_content: str) -> str:
                html_content = html_content.replace('<link rel="stylesheet" href="/resume/style.css">', '')
                css_tag = f'<style>{css_content}</style>'
                return html_content.replace('</head>', f'{css_tag}</head>')
            
            resume_html = prepare_html(resume_html)
            cover_letter_html = prepare_html(cover_letter_html)
            
            resume_html_path = output_dir / f"{base_resume_name}.html"
            cover_letter_html_path = output_dir / f"{base_cover_letter_name}.html"
            
            # Write HTML files
            resume_html_path.write_text(resume_html)
            cover_letter_html_path.write_text(cover_letter_html)
            logger.info("HTML files prepared with embedded CSS")
            
            # Define PDF paths
            resume_pdf_path = output_dir / f"{base_resume_name}.pdf"
            cover_letter_pdf_path = output_dir / f"{base_cover_letter_name}.pdf"
            
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                await page.set_viewport_size({"width": 1920, "height": 1080})
                
                # Function to generate PDF
                async def generate_pdf(html_path: Path, pdf_path: Path):
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
                
                # Generate PDFs
                logger.info("Generating PDF files...")
                await generate_pdf(resume_html_path, resume_pdf_path)
                await generate_pdf(cover_letter_html_path, cover_letter_pdf_path)
                await browser.close()
                
            # Verify files were created
            if not resume_pdf_path.exists() or not cover_letter_pdf_path.exists():
                raise ValueError("Failed to generate PDF files")
                
            logger.info("PDF generation completed")
            return resume_pdf_path, cover_letter_pdf_path
            
        except Exception as e:
            logger.error(f"Error generating PDFs: {str(e)}")
            raise

    def _create_resume_prompt(self, resume_content: str, form_data: Dict[str, str]) -> str:
        """Create prompt for resume customization"""
        additional_context = ""
        if form_data.get('relevant_experience'):
            additional_context = f"""
            Additional Relevant Experience:
            {form_data['relevant_experience']}
            """
            
        return f"""
        Given this resume content:
        {resume_content}
        
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
        
        IMPORTANT: 
        - Return ONLY the raw HTML content
        - Do NOT modify ANY HTML tags or attributes
        - Only change the text between tags
        - Keep all HTML structure exactly as provided
        - Do NOT wrap response in markdown code blocks
        - Start directly with <!DOCTYPE html>
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
        
        IMPORTANT: 
        - Return ONLY the raw HTML content
        - Use EXACTLY the HTML structure provided
        - Only modify the marked placeholders and letter body content
        - Do NOT modify any other HTML elements or attributes
        - Do NOT wrap response in markdown code blocks
        - Start directly with <!DOCTYPE html>
        """ 