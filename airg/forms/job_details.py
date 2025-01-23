from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField
from wtforms.validators import DataRequired, Length, ValidationError, Optional
import re

class JobDetailsForm(FlaskForm):
    job_title = StringField(
        'Job Title',
        validators=[
            DataRequired(),
            Length(min=3, max=100, message="Job title must be between 3 and 100 characters")
        ]
    )
    
    company_name = StringField(
        'Company Name',
        validators=[
            DataRequired(),
            Length(min=2, max=100, message="Company name must be between 2 and 100 characters")
        ]
    )
    
    hirer_name = StringField(
        'Hirer Name',
        validators=[Optional()]
    )
    
    hirer_gender = SelectField(
        'Hirer Gender',
        choices=[
            ('unknown', 'Unknown'),
            ('male', 'Male'),
            ('female', 'Female')
        ],
        default='unknown',
        validators=[Optional()]
    )
    
    job_description = TextAreaField(
        'Job Description',
        validators=[
            DataRequired(),
            Length(min=100, max=5000, message="Job description must be between 100 and 5000 characters")
        ]
    )
    
    company_overview = TextAreaField(
        'Company Overview',
        validators=[
            DataRequired(),
            Length(min=50, max=2000, message="Company overview must be between 50 and 2000 characters")
        ]
    )
    
    relevant_experience = TextAreaField(
        'Additional Relevant Experience',
        validators=[Optional()],
        description="Add any specific experience or skills relevant to this job that you want to emphasize"
    )
    
    def validate_job_title(self, field):
        if re.search(r'[<>{}]', field.data):
            raise ValidationError("Job title contains invalid characters")
    
    def validate_company_name(self, field):
        if re.search(r'[<>{}]', field.data):
            raise ValidationError("Company name contains invalid characters")
    
    def validate_hirer_name(self, field):
        if not re.match(r'^[A-Za-z\s\'-]+$', field.data):
            raise ValidationError("Hirer name can only contain letters, spaces, hyphens, and apostrophes") 