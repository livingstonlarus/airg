# AIRG

**AI Resume Generator** (v1.0)

Say "AIR-G" or "A.I.R.G" as you see fit.

AI Resume Generator. AIRG is a web app that crafts personalized resumes and cover letters using Google's Gemini AI. Input your resume, job details, and company info to generate tailored documents. Features real-time updates, dark mode, and PDF/HTML outputs. Ideal for job seekers aiming to match specific job requirements.

The application currently uses Gemini 2.0 Flash Thinking model, which is free to use while in its Experimental phase.

![Screenshot](./screenshot.png)

## Features

- Customizes your resume and cover letter based on job description and company details
- Uses Gemini 2.0 Flash Thinking for enhanced reasoning and customization
- Generates professional PDF documents with consistent styling
- Real-time progress updates during document generation
- Dark mode support (follows system preferences)
- Generates both HTML and PDF versions of documents
- Optional fields for hirer name and gender to personalize cover letters
- Additional field for relevant experience to emphasize specific skills

## Prerequisites

- Python 3.8+
- Gemini API key (obtain from [Google AI Studio](https://aistudio.google.com/app/apikey))
- A modern web browser (works well on mobile screen)

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/airg.git
cd airg
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

3. Install Chromium for PDF generation:
```bash
playwright install chromium
```

4. Copy the example environment file:
```bash
cp .env.example .env
```

5. Update the `.env` file with your settings:
   - Set `GEMINI_API_KEY` with your API key from Google AI Studio
   - Generate a secure random string for `SECRET_KEY` (you can use `python -c "import secrets; print(secrets.token_hex())"`)

## Usage

1. Run the application:
```bash
# Use 'python3' on macOS/Linux/FreeBSD or 'python' on Windows
python3 app.py
```

2. Open your browser and navigate to `http://localhost:5000`

3. Fill in the job details form:
   - Job Title
   - Company Name
   - Hirer Name (optional)
   - Hirer Gender (optional)
   - Job Description
   - Company Overview
   - Additional Relevant Experience (optional)

4. Click "Generate Documents" and wait for the process to complete

5. Download the generated resume and cover letter in PDF format

## Development Status

This project has been tested only locally, on macOS 15.2 (Sonoma). While it should work on other platforms, and maybe on the web,additional testing may be required.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the Apache License 2.0 - see the LICENSE file for details.

## Attribution

Attribution to Livingston Larus is appreciated. Please include the following notice in any distributions:

"Copyright 2024 Livingston Larus"

For more information, visit [Livingston Larus](https://livingstonlarus.com). 