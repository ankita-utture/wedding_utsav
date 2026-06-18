from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os
import smtplib
from dotenv import load_dotenv

def sendemailnewregistration(to_email, user_name):
    # 1. Configuration Settings
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    sender_email = os.environ.get("EMAIL_HOST_USER")
    sender_password = os.environ.get("EMAIL_HOST_PASSWORD")

    # 2. Setup the MIME Message
    message = MIMEMultipart("alternative")
    message["Subject"] = "Welcome to Wedding Utsav! 🎉"
    message["From"] = f"Wedding Utsav <{sender_email}>"
    message["To"] = to_email

    # 3. Read the HTML file safely
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    
        # 2. Look inside the emailtemplates folder right next to it
        template_path = os.path.join(script_dir, "emailtemplates", "registration.html")
        
        # 3. Read the file safely
        with open(template_path, "r", encoding="utf-8") as file:
            html_content = file.read()

        # 4. Replace the placeholder
        html_content = html_content.replace("{{user_name}}", user_name)
    
    except FileNotFoundError:
        print("❌ Error: registration.html file not found!")
        return

    # Plain text fallback
    text_content = f"Namaste {user_name},\n\nThank you for registering with Wedding Utsav! Your account has been successfully created."

    # Attach both parts
    message.attach(MIMEText(text_content, "plain"))
    message.attach(MIMEText(html_content, "html"))

    # 4. Connect to Server and Send Email
    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, to_email, message.as_string())
        print(f"✅ Success: Registration email sent to {to_email}")
    except Exception as e:
        print(f"❌ Error sending email: {e}")
    finally:
        server.quit()


# --- Example of usage ---
# sendemailnewregistration("customer@example.com", "Rahul")