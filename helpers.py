import functools
import datetime
import secrets
from flask import session, redirect, url_for, flash, request, render_template
from database import get_db_connection

# 1. RBAC Decorators
def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'danger')
            return redirect(url_for('login', next=request.url))
            
        # Check user status in database (support soft delete / suspension)
        conn = get_db_connection()
        user = conn.execute('SELECT status FROM users WHERE user_id = ?', (session['user_id'],)).fetchone()
        conn.close()
        
        if not user or user['status'] in ('suspended', 'deleted'):
            session.clear()
            flash('Your account is no longer active.', 'danger')
            return redirect(url_for('login'))
            
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please log in to access this page.', 'danger')
                return redirect(url_for('login'))
                
            user_role = session.get('role')
            if user_role not in allowed_roles:
                # Audit unauthorized access attempt
                log_audit(
                    user_id=session.get('user_id'),
                    action='UNAUTHORIZED_ACCESS_ATTEMPT',
                    entity_type='route',
                    details=f"User role '{user_role}' attempted to access '{request.path}' restricted to {allowed_roles}",
                    ip_address=request.remote_addr
                )
                return render_template('403.html'), 403
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# 2. Audit Logger
def log_audit(user_id, action, entity_type=None, entity_id=None, details=None, ip_address=None):
    try:
        from flask import has_request_context
        ip = ip_address
        if not ip and has_request_context():
            ip = request.remote_addr
        elif not ip:
            ip = '127.0.0.1'
            
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO audit_logs (user_id, action, entity_type, entity_id, details, ip_address)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, action, entity_type, entity_id, details, ip))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Audit log error: {e}")

# 3. Double-Booking & Availability Engine
def check_vendor_availability(conn, vendor_id, wedding_date, exclude_booking_id=None):
    """
    Checks if a vendor is available on a given date.
    Returns (is_available: bool, reason: str)
    """
    if not wedding_date:
        return False, "Wedding date is required."
        
    # Check manual blocks in vendor_availability
    blocked = conn.execute('''
        SELECT reason FROM vendor_availability
        WHERE vendor_id = ? AND blocked_date = ?
    ''', (vendor_id, wedding_date)).fetchone()
    
    if blocked:
        return False, f"Vendor is manually unavailable on this date ({blocked['reason']})."
        
    # Check active bookings on the same date
    query = '''
        SELECT booking_id FROM bookings
        WHERE vendor_id = ? 
          AND wedding_date = ?
          AND status IN ('PENDING', 'ACCEPTED', 'PAYMENT_PENDING', 'CONFIRMED')
    '''
    params = [vendor_id, wedding_date]
    if exclude_booking_id:
        query += ' AND booking_id != ?'
        params.append(exclude_booking_id)
        
    conflicting_booking = conn.execute(query, params).fetchone()
    if conflicting_booking:
        return False, "Vendor is already booked or has a pending reservation on this date."
        
    return True, "Available"

def block_vendor_date(conn, vendor_id, wedding_date, reason="Blocked by vendor"):
    try:
        conn.execute('''
            INSERT INTO vendor_availability (vendor_id, blocked_date, reason)
            VALUES (?, ?, ?)
        ''', (vendor_id, wedding_date, reason))
        conn.commit()
        return True, "Date blocked successfully."
    except Exception as e:
        return False, f"Could not block date: {str(e)}"

def unblock_vendor_date(conn, vendor_id, wedding_date):
    conn.execute('''
        DELETE FROM vendor_availability
        WHERE vendor_id = ? AND blocked_date = ?
    ''', (vendor_id, wedding_date))
    conn.commit()

def create_booking_atomic(customer_id, vendor_id, wedding_date, agreed_price, financials):
    """
    Executes within an atomic SQLite BEGIN IMMEDIATE transaction lock.
    Uses SQLite write locks and DB-level UNIQUE constraints to prevent
    concurrent race conditions when booking a vendor for a date.
    """
    import sqlite3
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        
        # 1. Check manual availability blocks
        blocked = conn.execute('''
            SELECT reason FROM vendor_availability
            WHERE vendor_id = ? AND blocked_date = ?
        ''', (vendor_id, wedding_date)).fetchone()
        
        if blocked:
            conn.rollback()
            conn.close()
            return None, f"Vendor is manually unavailable on this date ({blocked['reason']})."
            
        # 2. Check active conflicting bookings
        conflicting = conn.execute('''
            SELECT booking_id FROM bookings
            WHERE vendor_id = ? 
              AND wedding_date = ?
              AND status IN ('PENDING', 'ACCEPTED', 'PAYMENT_PENDING', 'CONFIRMED')
        ''', (vendor_id, wedding_date)).fetchone()
        
        if conflicting:
            conn.rollback()
            conn.close()
            return None, "Vendor is already booked or has a pending reservation on this date."
            
        # 3. Create booking (triggers SQLite partial unique index constraint if concurrent)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO bookings (
                customer_id, vendor_id, wedding_date, agreed_price, status,
                original_price, discount_amount, final_price, advance_required, advance_paid,
                remaining_amount, commission_rate, commission_amount, vendor_earnings
            ) VALUES (?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, 0.0, ?, ?, ?, ?)
        ''', (
            customer_id, vendor_id, wedding_date, financials['final_price'],
            financials['original_price'], financials['discount_amount'], financials['final_price'],
            financials['advance_required'], financials['final_price'],
            financials['commission_rate'], financials['commission_amount'], financials['vendor_earnings']
        ))
        
        booking_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return booking_id, "Success"
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return None, "Double-booking prevented by database constraint: another customer just reserved this date."
    except Exception as e:
        conn.rollback()
        conn.close()
        return None, f"Booking transaction failed: {str(e)}"

# 4. Financial Calculation Engine
def compute_booking_financials(base_price, discount_type='none', discount_value=0.0):
    """
    Computes exact financial breakdown based on original price, discounts,
    20% advance payment rule, and 5% platform commission rule on final price.
    """
    original_price = float(base_price or 0.0)
    discount_value = float(discount_value or 0.0)
    
    if discount_type == 'percentage':
        discount_amount = round(original_price * (discount_value / 100.0), 2)
    elif discount_type == 'fixed':
        discount_amount = min(discount_value, original_price)
    else:
        discount_amount = 0.0
        
    final_price = max(0.0, round(original_price - discount_amount, 2))
    advance_required = round(final_price * 0.20, 2) # 20% advance calculated from final agreed price
    commission_rate = 0.05
    commission_amount = round(final_price * commission_rate, 2) # 5% commission from final agreed price
    vendor_earnings = round(final_price - commission_amount, 2)
    
    return {
        'original_price': original_price,
        'discount_amount': discount_amount,
        'final_price': final_price,
        'advance_required': advance_required,
        'commission_rate': commission_rate,
        'commission_amount': commission_amount,
        'vendor_earnings': vendor_earnings
    }

# 5. Configurable Refund & Cancellation Engine
def calculate_cancellation_refund(booking, cancelled_by):
    """
    Documented Refund Rules:
    - If Cancelled by VENDOR: 100% refund of any advance paid.
    - If Cancelled by CUSTOMER:
      - For PENDING, ACCEPTED, PAYMENT_PENDING: No advance was paid yet -> Refund = 0.
      - For CONFIRMED:
        - > 14 days before wedding date: 100% of advance paid.
        - 3 to 14 days before wedding date: 50% of advance paid.
        - < 3 days before wedding date: 0% refund.
    Returns (refund_amount, policy_summary_text)
    """
    advance_paid = float(booking.get('advance_paid', 0.0) or 0.0)
    
    if cancelled_by == 'VENDOR':
        return advance_paid, "Vendor Cancellation Policy: 100% full refund of advance paid."
        
    # Cancelled by Customer
    status = booking.get('status')
    if status in ('PENDING', 'ACCEPTED', 'PAYMENT_PENDING'):
        return 0.0, "No advance payment was completed. Cancellation fee: ₹0.00."
        
    if status == 'CONFIRMED':
        wedding_date_str = booking.get('wedding_date')
        try:
            wedding_date = datetime.datetime.strptime(wedding_date_str, '%Y-%m-%d').date()
            today = datetime.date.today()
            days_left = (wedding_date - today).days
        except Exception:
            days_left = 15 # Default fallback if date parsing fails
            
        if days_left > 14:
            refund = advance_paid
            text = f"Early Cancellation (>14 days prior): 100% advance refund (₹{refund:,.2f})."
        elif 3 <= days_left <= 14:
            refund = round(advance_paid * 0.50, 2)
            text = f"Standard Cancellation (3-14 days prior): 50% advance refund (₹{refund:,.2f})."
        else:
            refund = 0.0
            text = "Late Cancellation (<3 days prior): 0% advance refund per cancellation policy."
            
        return refund, text
        
    return 0.0, "No refund applicable."

# 6. CSRF Protection Helpers
def generate_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(16)
    return session['_csrf_token']

def validate_csrf_token(token):
    session_token = session.get('_csrf_token')
    if not session_token or not token or session_token != token:
        return False
    return True

# 7. File Upload Validation
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'svg'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
