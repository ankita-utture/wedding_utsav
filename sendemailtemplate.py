from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os
import smtplib

def get_smtp_config():
    smtp_server = os.environ.get("EMAIL_HOST_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("EMAIL_HOST_PORT", 587))
    sender_email = os.environ.get("EMAIL_HOST_USER", "noreply@weddingutsav.com")
    sender_password = os.environ.get("EMAIL_HOST_PASSWORD", "")
    return smtp_server, smtp_port, sender_email, sender_password

def sendemailnewregistration(to_email, user_name):
    smtp_server, smtp_port, sender_email, sender_password = get_smtp_config()
    
    if not sender_password:
        print("ℹ️ SMTP password not configured. Email notification skipped safely.")
        return

    message = MIMEMultipart("alternative")
    message["Subject"] = "Welcome to Wedding Utsav! 🎉"
    message["From"] = f"Wedding Utsav <{sender_email}>"
    message["To"] = to_email

    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(script_dir, "emailtemplates", "registration.html")
        
        with open(template_path, "r", encoding="utf-8") as file:
            html_content = file.read()

        html_content = html_content.replace("{{user_name}}", user_name)
    except Exception as e:
        print(f"❌ Error reading registration email template: {e}")
        return

    text_content = f"Namaste {user_name},\n\nThank you for registering with Wedding Utsav! Your account has been successfully created."

    message.attach(MIMEText(text_content, "plain"))
    message.attach(MIMEText(html_content, "html"))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, to_email, message.as_string())
        server.quit()
        print(f"✅ Success: Registration email sent to {to_email}")
    except Exception as e:
        print(f"❌ Safe Email Error: Could not send email to {to_email}: {e}")

def send_booking_notification(to_email, recipient_name, booking_id, status, wedding_date, final_price, message_body):
    smtp_server, smtp_port, sender_email, sender_password = get_smtp_config()
    if not sender_password:
        return

    message = MIMEMultipart("alternative")
    message["Subject"] = f"Wedding Utsav — Booking Update #{booking_id} [{status}]"
    message["From"] = f"Wedding Utsav <{sender_email}>"
    message["To"] = to_email

    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(script_dir, "emailtemplates", "booking_notification.html")
        with open(template_path, "r", encoding="utf-8") as file:
            html_content = file.read()

        html_content = html_content.replace("{{recipient_name}}", recipient_name)\
                                   .replace("{{booking_id}}", str(booking_id))\
                                   .replace("{{status}}", str(status))\
                                   .replace("{{wedding_date}}", str(wedding_date))\
                                   .replace("{{final_price}}", f"{final_price:,.2f}")\
                                   .replace("{{message_body}}", message_body)
    except Exception as e:
        print(f"❌ Error reading booking notification template: {e}")
        return

    text_content = f"Namaste {recipient_name},\n\n{message_body}\nBooking ID: #{booking_id}\nStatus: {status}\nDate: {wedding_date}"

    message.attach(MIMEText(text_content, "plain"))
    message.attach(MIMEText(html_content, "html"))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, to_email, message.as_string())
        server.quit()
        print(f"✅ Success: Booking email sent to {to_email}")
    except Exception as e:
        print(f"❌ Safe Email Error: {e}")

def send_vendor_status_notification(to_email, vendor_name, status_message, notes=""):
    smtp_server, smtp_port, sender_email, sender_password = get_smtp_config()
    if not sender_password:
        return

    message = MIMEMultipart("alternative")
    message["Subject"] = "Wedding Utsav — Business Listing Status Update"
    message["From"] = f"Wedding Utsav <{sender_email}>"
    message["To"] = to_email

    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(script_dir, "emailtemplates", "vendor_status.html")
        with open(template_path, "r", encoding="utf-8") as file:
            html_content = file.read()

        html_content = html_content.replace("{{vendor_name}}", vendor_name)\
                                   .replace("{{status_message}}", status_message)\
                                   .replace("{{notes}}", notes)
    except Exception as e:
        print(f"❌ Error reading vendor status template: {e}")
        return

    text_content = f"Namaste {vendor_name},\n\n{status_message}\nNotes: {notes}"

    message.attach(MIMEText(text_content, "plain"))
    message.attach(MIMEText(html_content, "html"))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, to_email, message.as_string())
        server.quit()
        print(f"✅ Success: Vendor status email sent to {to_email}")
    except Exception as e:
        print(f"❌ Safe Email Error: {e}")