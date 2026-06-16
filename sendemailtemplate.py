from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os
import smtplib


def sendemailnewregistration(to_email, user_name):
    # 1. Configuration Settings
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    sender_email = "your_email@vivaahvibes.com"
    sender_password = "your_app_password"

    # 2. Setup the MIME Message
    message = MIMEMultipart("alternative")
    message["Subject"] = "Welcome to Vivaah Vibes! 🎉"
    message["From"] = f"Vivaah Vibes <{sender_email}>"
    message["To"] = to_email

    # 3. Read the HTML file safely
    try:
        # Opens 'template.html' from the same directory as this script
        with open("template.html", "r", encoding="utf-8") as file:
            html_content = file.read()

        # Replace the placeholder with the actual user's name
        html_content = html_content.replace("{{user_name}}", user_name)

    except FileNotFoundError:
        print("❌ Error: template.html file not found!")
        return

    # Plain text fallback
    text_content = f"Namaste {user_name},\n\nThank you for registering with Vivaah Vibes! Your account has been successfully created."

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