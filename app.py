from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, jsonify
from database import get_db_connection, init_db
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
import datetime
import secrets

# Custom Imports
from sendemailtemplate import (
    sendemailnewregistration,
    send_booking_notification,
    send_vendor_status_notification
)
from helpers import (
    login_required,
    role_required,
    log_audit,
    check_vendor_availability,
    block_vendor_date,
    unblock_vendor_date,
    create_booking_atomic,
    compute_booking_financials,
    calculate_cancellation_refund,
    generate_csrf_token,
    validate_csrf_token,
    allowed_file
)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'vivaah_vibes_secret_key_123')

# Security Session Configuration
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024 # 5MB max upload

# Ensure static/profile_pics directory exists
PROFILE_PICS_DIR = os.path.join(app.root_path, 'static', 'profile_pics')
os.makedirs(PROFILE_PICS_DIR, exist_ok=True)

# Ensure DB schema is initialized
init_db()

# Context processor for Jinja templates
@app.context_processor
def inject_global_vars():
    user = None
    if 'user_id' in session:
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE user_id = ?', (session['user_id'],)).fetchone()
        conn.close()
    return dict(current_user=user, csrf_token=generate_csrf_token())

# Middleware for CSRF Validation on POST Requests
@app.before_request
def csrf_protect():
    if request.method == "POST":
        # Skip CSRF check if testing or if token matches
        if app.config.get('TESTING'):
            return
        token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        if not validate_csrf_token(token):
            flash('Session expired or invalid security token. Please try again.', 'danger')
            return redirect(request.referrer or url_for('index'))

# Custom Error Handlers
@app.errorhandler(403)
def forbidden_error(e):
    return render_template('403.html'), 403

@app.errorhandler(404)
def not_found_error(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(e):
    return render_template('500.html'), 500

# Home router
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    role = session.get('role')
    if role == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif role == 'vendor':
        return redirect(url_for('vendor_dashboard'))
    else:
        return redirect(url_for('customer_dashboard'))

# Customer Dashboard
@app.route('/dashboard')
@login_required
@role_required('customer')
def customer_dashboard():
    user_id = session['user_id']
    conn = get_db_connection()
    
    # 1. Fetch user's budget details
    budget = conn.execute('SELECT * FROM budgets WHERE customer_id = ?', (user_id,)).fetchone()
    
    # 2. Fetch user's wedding date from rituals
    rituals = conn.execute('SELECT wedding_date FROM wedding_rituals WHERE customer_id = ?', (user_id,)).fetchone()
    wedding_date = rituals['wedding_date'] if rituals and rituals['wedding_date'] else session.get('wedding_date', 'Not set')
    
    # 3. Calculate summary metrics
    stats = {
        'total_budget': budget['total_budget'] if budget else 0.0,
        'wedding_date': wedding_date,
        'contacted_vendors': conn.execute('SELECT COUNT(*) FROM bookings WHERE customer_id = ?', (user_id,)).fetchone()[0],
        'pending_requests': conn.execute("SELECT COUNT(*) FROM bookings WHERE customer_id = ? AND status IN ('PENDING', 'ACCEPTED', 'PAYMENT_PENDING')", (user_id,)).fetchone()[0],
        'confirmed_bookings': conn.execute("SELECT COUNT(*) FROM bookings WHERE customer_id = ? AND status = 'CONFIRMED'", (user_id,)).fetchone()[0],
    }
    
    # Calculate committed budget (total agreed price of confirmed & completed bookings)
    committed = conn.execute("SELECT SUM(final_price) FROM bookings WHERE customer_id = ? AND status IN ('CONFIRMED', 'COMPLETED')", (user_id,)).fetchone()[0] or 0.0
    stats['committed_budget'] = committed
    stats['remaining_budget'] = max(0.0, stats['total_budget'] - committed) if stats['total_budget'] else 0.0
    
    # Fetch recent bookings timeline
    bookings = conn.execute('''
        SELECT b.*, v.business_name, v.service_type, v.contact_info, v.location, u.email as vendor_email
        FROM bookings b
        JOIN vendors v ON b.vendor_id = v.vendor_id
        JOIN users u ON v.user_id = u.user_id
        WHERE b.customer_id = ?
        ORDER BY b.created_at DESC
    ''', (user_id,)).fetchall()
    
    conn.close()
    return render_template('index.html', stats=stats, bookings=bookings)

# Vendor Dashboard
@app.route('/vendor/dashboard')
@login_required
@role_required('vendor')
def vendor_dashboard():
    user_id = session['user_id']
    conn = get_db_connection()
    
    vendor = conn.execute('SELECT * FROM vendors WHERE user_id = ?', (user_id,)).fetchone()
    
    bookings = []
    stats = {
        'total_inquiries': 0,
        'pending_requests': 0,
        'confirmed_bookings': 0,
        'completed_bookings': 0,
        'avg_rating': 0.0,
        'review_count': 0,
        'total_booking_value': 0.0,
        'platform_commission': 0.0,
        'vendor_earnings': 0.0
    }
    blocked_dates = []
    
    if vendor:
        v_id = vendor['vendor_id']
        bookings = conn.execute('''
            SELECT b.*, u.full_name as customer_name, u.phone as customer_phone, u.email as customer_email, u.status as customer_status
            FROM bookings b 
            JOIN users u ON b.customer_id = u.user_id 
            WHERE b.vendor_id = ?
            ORDER BY b.created_at DESC
        ''', (v_id,)).fetchall()
        
        stats['total_inquiries'] = len(bookings)
        stats['pending_requests'] = sum(1 for b in bookings if b['status'] == 'PENDING')
        stats['confirmed_bookings'] = sum(1 for b in bookings if b['status'] == 'CONFIRMED')
        stats['completed_bookings'] = sum(1 for b in bookings if b['status'] == 'COMPLETED')
        
        # Calculate financial stats for confirmed & completed bookings
        financials = conn.execute('''
            SELECT SUM(final_price) as gross, SUM(commission_amount) as comm, SUM(vendor_earnings) as earn
            FROM bookings
            WHERE vendor_id = ? AND status IN ('CONFIRMED', 'COMPLETED')
        ''', (v_id,)).fetchone()
        
        stats['total_booking_value'] = financials['gross'] or 0.0
        stats['platform_commission'] = financials['comm'] or 0.0
        stats['vendor_earnings'] = financials['earn'] or 0.0
        
        # Fetch ratings
        review_stat = conn.execute('''
            SELECT AVG(rating) as avg_r, COUNT(*) as count_r FROM reviews WHERE vendor_id = ?
        ''', (v_id,)).fetchone()
        stats['avg_rating'] = round(review_stat['avg_r'] or 0.0, 1)
        stats['review_count'] = review_stat['count_r'] or 0
        
        # Fetch manually blocked dates
        blocked_dates = conn.execute('SELECT * FROM vendor_availability WHERE vendor_id = ? ORDER BY blocked_date ASC', (v_id,)).fetchall()
        
    conn.close()
    return render_template('vendor_dashboard.html', vendor=vendor, bookings=bookings, stats=stats, blocked_dates=blocked_dates)

# Admin Dashboard
@app.route('/admin/dashboard')
@login_required
@role_required('admin')
def admin_dashboard():
    conn = get_db_connection()
    
    stats = {
        'total_customers': conn.execute("SELECT COUNT(*) FROM users WHERE role = 'customer' AND status != 'deleted'").fetchone()[0],
        'total_vendors': conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0],
        'verified_vendors': conn.execute("SELECT COUNT(*) FROM vendors WHERE verification_status = 'VERIFIED'").fetchone()[0],
        'pending_approvals': conn.execute("SELECT COUNT(*) FROM vendors WHERE verification_status = 'PENDING'").fetchone()[0],
        'total_bookings': conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0],
        'confirmed_bookings': conn.execute("SELECT COUNT(*) FROM bookings WHERE status = 'CONFIRMED'").fetchone()[0],
        'completed_bookings': conn.execute("SELECT COUNT(*) FROM bookings WHERE status = 'COMPLETED'").fetchone()[0],
        'gross_booking_value': conn.execute("SELECT SUM(final_price) FROM bookings WHERE status IN ('CONFIRMED', 'COMPLETED')").fetchone()[0] or 0.0,
        'platform_revenue': conn.execute("SELECT SUM(commission_amount) FROM bookings WHERE status IN ('CONFIRMED', 'COMPLETED')").fetchone()[0] or 0.0,
        'total_vendor_payouts': conn.execute("SELECT SUM(vendor_earnings) FROM bookings WHERE status IN ('CONFIRMED', 'COMPLETED')").fetchone()[0] or 0.0,
        'total_refunds': conn.execute("SELECT SUM(refund_amount) FROM bookings WHERE refund_amount > 0").fetchone()[0] or 0.0,
    }
    
    # Pending Vendors
    pending_vendors = conn.execute('''
        SELECT v.*, u.full_name as owner_name, u.email, u.phone 
        FROM vendors v 
        JOIN users u ON v.user_id = u.user_id 
        WHERE v.verification_status IN ('PENDING', 'CHANGES_REQUESTED')
        ORDER BY v.vendor_id DESC
    ''').fetchall()
    
    # All Vendors
    all_vendors = conn.execute('''
        SELECT v.*, u.full_name as owner_name, u.email, u.phone 
        FROM vendors v 
        JOIN users u ON v.user_id = u.user_id 
        ORDER BY v.vendor_id DESC
    ''').fetchall()
    
    # All Users
    all_users = conn.execute('SELECT * FROM users ORDER BY user_id DESC').fetchall()
    
    # All Bookings with Payment Status
    all_bookings = conn.execute('''
        SELECT b.*, cu.full_name as customer_name, v.business_name
        FROM bookings b
        LEFT JOIN users cu ON b.customer_id = cu.user_id
        LEFT JOIN vendors v ON b.vendor_id = v.vendor_id
        ORDER BY b.created_at DESC
    ''').fetchall()
    
    # Audit Logs
    audit_logs = conn.execute('''
        SELECT a.*, u.email as user_email
        FROM audit_logs a
        LEFT JOIN users u ON a.user_id = u.user_id
        ORDER BY a.timestamp DESC LIMIT 100
    ''').fetchall()
    
    # Reviews
    reviews = conn.execute('''
        SELECT r.*, u.full_name as customer_name, v.business_name
        FROM reviews r
        JOIN users u ON r.customer_id = u.user_id
        JOIN vendors v ON r.vendor_id = v.vendor_id
        ORDER BY r.created_at DESC
    ''').fetchall()
    
    conn.close()
    return render_template('admin.html', stats=stats, pending_vendors=pending_vendors, 
                           all_vendors=all_vendors, all_users=all_users, 
                           all_bookings=all_bookings, audit_logs=audit_logs, reviews=reviews)

# Registration
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        email = request.form['email'].strip().lower()
        phone = request.form['phone'].strip()
        password = request.form['password']
        role = request.form['role']
        city = request.form['city'].strip()
        
        if role not in ('customer', 'vendor'):
            flash('Invalid user role selected!', 'danger')
            return render_template('register.html')
            
        hashed_password = generate_password_hash(password)
        
        conn = get_db_connection()
        try:
            existing = conn.execute('SELECT email FROM users WHERE email = ?', (email,)).fetchone()
            if existing:
                flash('Email already registered! Please log in.', 'danger')
                return render_template('register.html')
            
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (full_name, email, phone, password_hash, role, city, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')
            ''', (full_name, email, phone, hashed_password, role, city))
            user_id = cursor.lastrowid
            
            if role == 'vendor':
                business_name = request.form.get('business_name', '').strip()
                service_type = request.form.get('service_type', 'venue')
                base_price = float(request.form.get('base_price', 0) or 0)
                description = request.form.get('description', '').strip()
                
                # All new vendors start strictly as PENDING
                cursor.execute('''
                    INSERT INTO vendors (user_id, business_name, service_type, location, base_price, description, contact_info, is_verified, verification_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'PENDING')
                ''', (user_id, business_name, service_type, f"{city}, India", base_price, description, phone))
            
            conn.commit()
            
            log_audit(user_id, 'USER_REGISTERED', 'user', user_id, f"Role: {role}, Email: {email}")
            sendemailnewregistration(email, full_name)
            
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            conn.rollback()
            flash(f'An error occurred during registration: {str(e)}', 'danger')
        finally:
            conn.close()
            
    return render_template('register.html')

# Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password_hash'], password):
            if user['status'] == 'suspended':
                flash('Your account has been suspended by an administrator. Please contact support.', 'danger')
                return render_template('login.html')
            elif user['status'] == 'deleted':
                flash('This account was deleted.', 'danger')
                return render_template('login.html')
                
            session['user_id'] = user['user_id']
            session['email'] = user['email']
            session['role'] = user['role']
            session['name'] = user['full_name']
            
            log_audit(user['user_id'], 'USER_LOGIN', 'user', user['user_id'], f"Role: {user['role']}")
            flash(f"Welcome back, {user['full_name']}!", 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid email or password!', 'danger')
            
    return render_template('login.html')

# Logout
@app.route('/logout')
def logout():
    if 'user_id' in session:
        log_audit(session['user_id'], 'USER_LOGOUT', 'user', session['user_id'])
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))

# Budget Planner
@app.route('/budget', methods=['GET', 'POST'])
@login_required
@role_required('customer')
def budget_page():
    user_id = session['user_id']
    conn = get_db_connection()
    
    if request.method == 'POST':
        total_budget = float(request.form['total_budget'])
        wedding_date = request.form['wedding_date']
        selected_services = request.form.getlist('services')
        
        if not selected_services:
            flash('Please select at least one wedding service!', 'danger')
            conn.close()
            return redirect(url_for('budget_page'))
            
        weights = {'venue': 50, 'catering': 20, 'dj': 15, 'makeup': 15}
        total_weight = sum(weights[s] for s in selected_services)
        
        venue_alloc = (weights['venue'] / total_weight) * total_budget if 'venue' in selected_services else 0.0
        catering_alloc = (weights['catering'] / total_weight) * total_budget if 'catering' in selected_services else 0.0
        dj_alloc = (weights['dj'] / total_weight) * total_budget if 'dj' in selected_services else 0.0
        makeup_alloc = (weights['makeup'] / total_weight) * total_budget if 'makeup' in selected_services else 0.0
        
        conn.execute('''
            INSERT INTO budgets (customer_id, total_budget, venue_allocated, catering_allocated, dj_allocated, makeup_allocated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(customer_id) DO UPDATE SET
                total_budget = excluded.total_budget,
                venue_allocated = excluded.venue_allocated,
                catering_allocated = excluded.catering_allocated,
                dj_allocated = excluded.dj_allocated,
                makeup_allocated = excluded.makeup_allocated
        ''', (user_id, total_budget, venue_alloc, catering_alloc, dj_alloc, makeup_alloc))
        
        conn.commit()
        session['wedding_date'] = wedding_date
        
        log_audit(user_id, 'BUDGET_UPDATED', 'budget', user_id, f"Total Budget: ₹{total_budget:,.2f}")
        flash('Budget split calculated and saved successfully!', 'success')
        conn.close()
        return redirect(url_for('budget_page'))
        
    budget = conn.execute('SELECT * FROM budgets WHERE customer_id = ?', (user_id,)).fetchone()
    conn.close()
    return render_template('budget.html', budget=budget)

# Wedding Rituals Route
@app.route('/rituals', methods=['GET', 'POST'])
@login_required
@role_required('customer')
def rituals_page():
    user_id = session['user_id']
    conn = get_db_connection()
    
    if request.method == 'POST':
        haldi_date = request.form.get('haldi_date', '')
        mehendi_date = request.form.get('mehendi_date', '')
        sangeet_date = request.form.get('sangeet_date', '')
        wedding_date = request.form.get('wedding_date', '')
        
        conn.execute('''
            INSERT INTO wedding_rituals (customer_id, haldi_date, mehendi_date, sangeet_date, wedding_date)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(customer_id) DO UPDATE SET
                haldi_date = excluded.haldi_date,
                mehendi_date = excluded.mehendi_date,
                sangeet_date = excluded.sangeet_date,
                wedding_date = excluded.wedding_date
        ''', (user_id, haldi_date, mehendi_date, sangeet_date, wedding_date))
        conn.commit()
        
        if wedding_date:
            session['wedding_date'] = wedding_date
            
        log_audit(user_id, 'RITUALS_UPDATED', 'rituals', user_id, f"Wedding Date: {wedding_date}")
        flash('Wedding rituals schedule updated successfully!', 'success')
        conn.close()
        return redirect(url_for('rituals_page'))
        
    rituals = conn.execute('SELECT * FROM wedding_rituals WHERE customer_id = ?', (user_id,)).fetchone()
    conn.close()
    return render_template('rituals.html', rituals=rituals)

# Browse & Search Vendors
@app.route('/vendors')
@login_required
@role_required('customer')
def search_vendors():
    category = request.args.get('type', 'venue')
    selected_date = request.args.get('date', session.get('wedding_date', ''))
    guest_count = int(request.args.get('guests', 0) or 0)
    
    user_id = session['user_id']
    conn = get_db_connection()
    
    budget = conn.execute('SELECT * FROM budgets WHERE customer_id = ?', (user_id,)).fetchone()
    default_limit = None
    if budget:
        if category == 'venue': default_limit = budget['venue_allocated']
        elif category == 'catering': default_limit = budget['catering_allocated']
        elif category == 'dj': default_limit = budget['dj_allocated']
        elif category == 'makeup': default_limit = budget['makeup_allocated']
            
    budget_limit = request.args.get('budget_limit')
    if budget_limit == '' or budget_limit is None:
        budget_limit = default_limit
    else:
        budget_limit = float(budget_limit)
        
    if budget_limit is None or budget_limit == 0:
        budget_limit = 99999999.0
        
    # Query ONLY VERIFIED vendors
    query = '''
        SELECT v.*, AVG(r.rating) as avg_rating, COUNT(r.rating) as review_count
        FROM vendors v
        LEFT JOIN reviews r ON v.vendor_id = r.vendor_id
        WHERE v.service_type = ? 
          AND v.verification_status = 'VERIFIED'
          AND v.base_price <= ?
          AND (v.capacity IS NULL OR v.capacity >= ?)
        GROUP BY v.vendor_id
    '''
    
    raw_vendors = conn.execute(query, (category, budget_limit, guest_count)).fetchall()
    
    # Process vendors: check availability on selected date & compute discounted price
    vendors_list = []
    for v in raw_vendors:
        v_dict = dict(v)
        
        # Check backend availability if date is selected
        if selected_date:
            is_avail, reason = check_vendor_availability(conn, v['vendor_id'], selected_date)
            v_dict['is_available'] = is_avail
            v_dict['unavailability_reason'] = reason
        else:
            v_dict['is_available'] = True
            v_dict['unavailability_reason'] = ""
            
        # Compute discount & pricing preview
        financials = compute_booking_financials(v['base_price'], v['discount_type'], v['discount_value'])
        v_dict['original_price'] = financials['original_price']
        v_dict['discount_amount'] = financials['discount_amount']
        v_dict['final_price'] = financials['final_price']
        v_dict['advance_required'] = financials['advance_required']
        
        vendors_list.append(v_dict)
        
    conn.close()
    
    filters = {
        'date': selected_date,
        'guests': guest_count,
        'budget_limit': budget_limit if budget_limit != 99999999.0 else ''
    }
    
    return render_template('vendors.html', category=category, vendors=vendors_list, filters=filters, default_limit=default_limit)

# Connect & Booking Request (PENDING)
@app.route('/connect/<int:vendor_id>', methods=['POST'])
@login_required
@role_required('customer')
def connect_vendor(vendor_id):
    wedding_date = request.form.get('wedding_date', '').strip()
    customer_id = session['user_id']
    
    if not wedding_date:
        flash('Wedding date is required to request a booking!', 'danger')
        return redirect(url_for('customer_dashboard'))
        
    conn = get_db_connection()
    vendor = conn.execute('SELECT * FROM vendors WHERE vendor_id = ? AND verification_status = "VERIFIED"', (vendor_id,)).fetchone()
    if not vendor:
        conn.close()
        flash('Selected vendor is not active or verified.', 'danger')
        return redirect(url_for('customer_dashboard'))
        
    financials = compute_booking_financials(vendor['base_price'], vendor['discount_type'], vendor['discount_value'])
    vendor_user_id = vendor['user_id']
    conn.close()
    
    # Atomic DB Transaction Lock & Partial Unique Constraint Check
    booking_id, msg = create_booking_atomic(customer_id, vendor_id, wedding_date, financials['final_price'], financials)
    if not booking_id:
        flash(f'Booking request failed: {msg}', 'danger')
        return redirect(url_for('search_vendors', type=vendor['service_type']))
        
    conn = get_db_connection()
    vendor_user = conn.execute('SELECT email, full_name FROM users WHERE user_id = ?', (vendor_user_id,)).fetchone()
    customer_user = conn.execute('SELECT email, full_name FROM users WHERE user_id = ?', (customer_id,)).fetchone()
    conn.close()
    
    log_audit(customer_id, 'BOOKING_REQUESTED', 'booking', booking_id, f"Vendor ID: {vendor_id}, Date: {wedding_date}, Final Price: ₹{financials['final_price']:,.2f}")
    
    if vendor_user:
        send_booking_notification(
            to_email=vendor_user['email'],
            recipient_name=vendor_user['full_name'],
            booking_id=booking_id,
            status='PENDING',
            wedding_date=wedding_date,
            final_price=financials['final_price'],
            message_body=f"You have received a new booking request from {customer_user['full_name']} for {wedding_date}."
        )
        
    flash('Booking request submitted to vendor! Status is PENDING.', 'success')
    return redirect(url_for('profile'))

# Vendor Accept / Reject Request
@app.route('/booking/<int:booking_id>/respond', methods=['POST'])
@login_required
@role_required('vendor')
def respond_booking(booking_id):
    action = request.form.get('action') # 'accept' or 'reject'
    vendor_user_id = session['user_id']
    
    conn = get_db_connection()
    vendor = conn.execute('SELECT vendor_id FROM vendors WHERE user_id = ?', (vendor_user_id,)).fetchone()
    if not vendor:
        conn.close()
        abort(403)
        
    booking = conn.execute('SELECT * FROM bookings WHERE booking_id = ? AND vendor_id = ?', (booking_id, vendor['vendor_id'])).fetchone()
    if not booking:
        conn.close()
        flash('Booking record not found or access denied.', 'danger')
        return redirect(url_for('vendor_dashboard'))
        
    if booking['status'] != 'PENDING':
        conn.close()
        flash(f'Cannot respond to booking in status {booking["status"]}.', 'warning')
        return redirect(url_for('vendor_dashboard'))
        
    customer_user = conn.execute('SELECT email, full_name FROM users WHERE user_id = ?', (booking['customer_id'],)).fetchone()
    
    if action == 'accept':
        conn.execute("UPDATE bookings SET status = 'PAYMENT_PENDING', updated_at = CURRENT_TIMESTAMP WHERE booking_id = ?", (booking_id,))
        conn.commit()
        log_audit(session['user_id'], 'BOOKING_ACCEPTED', 'booking', booking_id, "Status changed to PAYMENT_PENDING")
        
        if customer_user:
            send_booking_notification(
                to_email=customer_user['email'],
                recipient_name=customer_user['full_name'],
                booking_id=booking_id,
                status='PAYMENT_PENDING',
                wedding_date=booking['wedding_date'],
                final_price=booking['final_price'],
                message_body="Great news! Your booking request was ACCEPTED by the vendor. Please complete the 20% advance payment to confirm."
            )
        flash('Booking request accepted! Customer has been requested to pay advance.', 'success')
        
    elif action == 'reject':
        conn.execute("UPDATE bookings SET status = 'REJECTED', updated_at = CURRENT_TIMESTAMP WHERE booking_id = ?", (booking_id,))
        conn.commit()
        log_audit(session['user_id'], 'BOOKING_REJECTED', 'booking', booking_id, "Status changed to REJECTED")
        
        if customer_user:
            send_booking_notification(
                to_email=customer_user['email'],
                recipient_name=customer_user['full_name'],
                booking_id=booking_id,
                status='REJECTED',
                wedding_date=booking['wedding_date'],
                final_price=booking['final_price'],
                message_body="Unfortunately, the vendor has rejected your booking request."
            )
        flash('Booking request rejected.', 'info')
        
    conn.close()
    return redirect(url_for('vendor_dashboard'))

# Customer Advance Payment Sandbox
@app.route('/booking/<int:booking_id>/pay', methods=['GET', 'POST'])
@login_required
@role_required('customer')
def pay_advance(booking_id):
    customer_id = session['user_id']
    conn = get_db_connection()
    
    booking = conn.execute('''
        SELECT b.*, v.business_name, v.service_type, v.user_id as vendor_user_id
        FROM bookings b
        JOIN vendors v ON b.vendor_id = v.vendor_id
        WHERE b.booking_id = ? AND b.customer_id = ?
    ''', (booking_id, customer_id)).fetchone()
    
    if not booking:
        conn.close()
        flash('Booking not found.', 'danger')
        return redirect(url_for('profile'))
        
    if booking['status'] != 'PAYMENT_PENDING':
        conn.close()
        flash('Payment is not currently pending for this booking.', 'warning')
        return redirect(url_for('profile'))
        
    if request.method == 'POST':
        payment_method = request.form.get('payment_method', 'MockCard')
        
        # Simulate payment gateway transaction (no sensitive credentials saved!)
        tx_ref = f"MOCK_TXN_{booking_id}_{secrets.token_hex(4).upper()}"
        advance_amt = booking['advance_required']
        
        # 1. Record payment
        conn.execute('''
            INSERT INTO payments (booking_id, customer_id, amount, payment_type, status, transaction_reference, payment_gateway, paid_at)
            VALUES (?, ?, ?, 'ADVANCE', 'SUCCESS', ?, ?, CURRENT_TIMESTAMP)
        ''', (booking_id, customer_id, advance_amt, tx_ref, f"MockGateway-{payment_method}"))
        
        # 2. Update booking status to CONFIRMED
        rem_amt = round(booking['final_price'] - advance_amt, 2)
        conn.execute('''
            UPDATE bookings 
            SET status = 'CONFIRMED', advance_paid = ?, remaining_amount = ?, updated_at = CURRENT_TIMESTAMP
            WHERE booking_id = ?
        ''', (advance_amt, rem_amt, booking_id))
        
        # 3. Create or update 5% Platform Commission record
        conn.execute('''
            INSERT INTO commissions (booking_id, vendor_id, gross_amount, commission_rate, commission_amount, vendor_earnings, status)
            VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
            ON CONFLICT(booking_id) DO UPDATE SET status = 'PENDING'
        ''', (booking_id, booking['vendor_id'], booking['final_price'], booking['commission_rate'], booking['commission_amount'], booking['vendor_earnings']))
        
        conn.commit()
        
        # Send notifications
        customer_user = conn.execute('SELECT email, full_name FROM users WHERE user_id = ?', (customer_id,)).fetchone()
        vendor_user = conn.execute('SELECT email, full_name FROM users WHERE user_id = ?', (booking['vendor_user_id'],)).fetchone()
        conn.close()
        
        log_audit(customer_id, 'PAYMENT_SUCCESSFUL', 'payment', booking_id, f"Txn Ref: {tx_ref}, Advance Paid: ₹{advance_amt:,.2f}")
        
        if customer_user:
            send_booking_notification(
                customer_user['email'], customer_user['full_name'], booking_id, 'CONFIRMED',
                booking['wedding_date'], booking['final_price'],
                f"Payment successful! ₹{advance_amt:,.2f} advance paid. Your booking for {booking['business_name']} is CONFIRMED."
            )
        if vendor_user:
            send_booking_notification(
                vendor_user['email'], vendor_user['full_name'], booking_id, 'CONFIRMED',
                booking['wedding_date'], booking['final_price'],
                f"Customer paid ₹{advance_amt:,.2f} advance. Booking #{booking_id} is now CONFIRMED!"
            )
            
        flash('Advance payment successful! Your booking is now CONFIRMED.', 'success')
        return redirect(url_for('profile'))
        
    conn.close()
    return render_template('profile.html', pay_booking=booking)

# Booking Cancellation (Customer / Vendor)
@app.route('/booking/<int:booking_id>/cancel', methods=['POST'])
@login_required
def cancel_booking(booking_id):
    user_id = session['user_id']
    role = session['role']
    reason = request.form.get('cancellation_reason', 'No reason provided').strip()
    
    conn = get_db_connection()
    booking = conn.execute('SELECT * FROM bookings WHERE booking_id = ?', (booking_id,)).fetchone()
    
    if not booking:
        conn.close()
        flash('Booking not found.', 'danger')
        return redirect(url_for('index'))
        
    # Authorization Ownership check
    if role == 'customer' and booking['customer_id'] != user_id:
        conn.close()
        abort(403)
    elif role == 'vendor':
        vendor = conn.execute('SELECT vendor_id FROM vendors WHERE user_id = ?', (user_id,)).fetchone()
        if not vendor or booking['vendor_id'] != vendor['vendor_id']:
            conn.close()
            abort(403)
            
    if booking['status'] in ('COMPLETED', 'CANCELLED_BY_CUSTOMER', 'CANCELLED_BY_VENDOR', 'REJECTED'):
        conn.close()
        flash('This booking cannot be cancelled.', 'warning')
        return redirect(url_for('profile' if role == 'customer' else 'vendor_dashboard'))
        
    cancelled_by = 'VENDOR' if role == 'vendor' else 'CUSTOMER'
    new_status = 'CANCELLED_BY_VENDOR' if role == 'vendor' else 'CANCELLED_BY_CUSTOMER'
    
    # Compute Refund
    refund_amount, policy_msg = calculate_cancellation_refund(booking, cancelled_by)
    
    conn.execute('''
        UPDATE bookings 
        SET status = ?, cancelled_by = ?, cancellation_reason = ?, cancellation_time = CURRENT_TIMESTAMP, refund_amount = ?, updated_at = CURRENT_TIMESTAMP
        WHERE booking_id = ?
    ''', (new_status, cancelled_by, reason, refund_amount, booking_id))
    
    # Update payment record status if refund > 0
    if refund_amount > 0:
        conn.execute('''
            UPDATE payments SET status = 'REFUNDED' WHERE booking_id = ?
        ''', (booking_id,))
        
    # Update commission status
    conn.execute("UPDATE commissions SET status = 'CANCELLED' WHERE booking_id = ?", (booking_id,))
    
    conn.commit()
    log_audit(user_id, 'BOOKING_CANCELLED', 'booking', booking_id, f"Cancelled by {cancelled_by}. Refund: ₹{refund_amount:,.2f}. Reason: {reason}")
    conn.close()
    
    flash(f"Booking cancelled successfully. {policy_msg}", 'info')
    return redirect(url_for('profile' if role == 'customer' else 'vendor_dashboard'))

# Vendor Complete Booking
@app.route('/booking/<int:booking_id>/complete', methods=['POST'])
@login_required
@role_required('vendor')
def complete_booking(booking_id):
    vendor_user_id = session['user_id']
    conn = get_db_connection()
    
    vendor = conn.execute('SELECT vendor_id FROM vendors WHERE user_id = ?', (vendor_user_id,)).fetchone()
    if not vendor:
        conn.close()
        abort(403)
        
    booking = conn.execute('SELECT * FROM bookings WHERE booking_id = ? AND vendor_id = ?', (booking_id, vendor['vendor_id'])).fetchone()
    if not booking or booking['status'] != 'CONFIRMED':
        conn.close()
        flash('Only CONFIRMED bookings can be marked as COMPLETED.', 'warning')
        return redirect(url_for('vendor_dashboard'))
        
    conn.execute("UPDATE bookings SET status = 'COMPLETED', updated_at = CURRENT_TIMESTAMP WHERE booking_id = ?", (booking_id,))
    conn.execute("UPDATE commissions SET status = 'EARNED' WHERE booking_id = ?", (booking_id,))
    conn.commit()
    
    log_audit(vendor_user_id, 'BOOKING_COMPLETED', 'booking', booking_id, "Booking marked COMPLETED")
    conn.close()
    
    flash('Booking service marked COMPLETED! Final earnings recorded.', 'success')
    return redirect(url_for('vendor_dashboard'))

# Vendor Business Settings & Discounts
@app.route('/vendor/settings', methods=['POST'])
@login_required
@role_required('vendor')
def update_vendor_settings():
    user_id = session['user_id']
    business_name = request.form['business_name'].strip()
    base_price = float(request.form['base_price'])
    discount_type = request.form.get('discount_type', 'none')
    discount_value = float(request.form.get('discount_value', 0.0) or 0.0)
    capacity = int(request.form.get('capacity', 0) or 0) if request.form.get('capacity') else None
    location = request.form['location'].strip()
    contact_info = request.form['contact_info'].strip()
    description = request.form['description'].strip()
    
    conn = get_db_connection()
    conn.execute('''
        UPDATE vendors 
        SET business_name = ?, base_price = ?, discount_type = ?, discount_value = ?, capacity = ?, location = ?, contact_info = ?, description = ?
        WHERE user_id = ?
    ''', (business_name, base_price, discount_type, discount_value, capacity, location, contact_info, description, user_id))
    conn.commit()
    
    log_audit(user_id, 'VENDOR_SETTINGS_UPDATED', 'vendor', user_id, f"Base Price: ₹{base_price}, Discount: {discount_type} {discount_value}")
    conn.close()
    
    flash('Business profile and pricing updated successfully!', 'success')
    return redirect(url_for('vendor_dashboard'))

# Vendor Availability Date Block / Unblock
@app.route('/vendor/availability', methods=['POST'])
@login_required
@role_required('vendor')
def toggle_availability():
    user_id = session['user_id']
    action = request.form.get('action') # 'block' or 'unblock'
    blocked_date = request.form.get('blocked_date')
    reason = request.form.get('reason', 'Blocked by vendor')
    
    conn = get_db_connection()
    vendor = conn.execute('SELECT vendor_id FROM vendors WHERE user_id = ?', (user_id,)).fetchone()
    if not vendor:
        conn.close()
        abort(403)
        
    if action == 'block':
        success, msg = block_vendor_date(conn, vendor['vendor_id'], blocked_date, reason)
        flash(msg, 'success' if success else 'danger')
    elif action == 'unblock':
        unblock_vendor_date(conn, vendor['vendor_id'], blocked_date)
        flash('Date unblocked successfully.', 'info')
        
    conn.close()
    return redirect(url_for('vendor_dashboard'))

# Customer Profile & Review Submission
@app.route('/profile')
@login_required
def profile():
    user_id = session['user_id']
    conn = get_db_connection()
    
    budget = conn.execute('SELECT * FROM budgets WHERE customer_id = ?', (user_id,)).fetchone()
    
    bookings = conn.execute('''
        SELECT b.*, v.business_name, v.service_type, v.contact_info, v.location, v.vendor_id
        FROM bookings b 
        JOIN vendors v ON b.vendor_id = v.vendor_id 
        WHERE b.customer_id = ?
        ORDER BY b.created_at DESC
    ''', (user_id,)).fetchall()
    
    # Check existing reviews by customer
    existing_reviews = conn.execute('SELECT booking_id FROM reviews WHERE customer_id = ?', (user_id,)).fetchall()
    reviewed_booking_ids = {r['booking_id'] for r in existing_reviews if r['booking_id']}
    
    conn.close()
    return render_template('profile.html', budget=budget, bookings=bookings, reviewed_booking_ids=reviewed_booking_ids)

# Profile Photo Upload Handler
@app.route('/upload_profile_pic', methods=['POST'])
@login_required
def upload_profile_pic():
    if 'profile_pic' not in request.files:
        flash('No file provided!', 'danger')
        return redirect(url_for('profile'))
        
    file = request.files['profile_pic']
    if file.filename == '':
        flash('No selected file!', 'danger')
        return redirect(url_for('profile'))
        
    if file and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = secure_filename(f"user_{session['user_id']}_{secrets.token_hex(4)}.{ext}")
        file_path = os.path.join(app.root_path, 'static', 'profile_pics', filename)
        file.save(file_path)
        
        pic_url = f"/static/profile_pics/{filename}"
        
        conn = get_db_connection()
        conn.execute('UPDATE users SET profile_pic_url = ? WHERE user_id = ?', (pic_url, session['user_id']))
        conn.commit()
        conn.close()
        
        log_audit(session['user_id'], 'PROFILE_PIC_UPLOADED', 'user', session['user_id'], pic_url)
        flash('Profile picture uploaded successfully!', 'success')
    else:
        flash('Invalid file type! Allowed formats: JPG, PNG, WEBP, SVG.', 'danger')
        
    return redirect(url_for('profile'))

# Customer Soft Delete Account
@app.route('/profile/delete_account', methods=['POST'])
@login_required
@role_required('customer')
def delete_account():
    user_id = session['user_id']
    conn = get_db_connection()
    
    # Soft Delete: update status to 'deleted', preserve business & payment records
    conn.execute("UPDATE users SET status = 'deleted' WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    log_audit(user_id, 'ACCOUNT_SOFT_DELETED', 'user', user_id, "Customer soft deleted account")
    session.clear()
    flash('Your account has been deleted. Your booking history has been safely archived.', 'info')
    return redirect(url_for('login'))

# Review & Rating Submission (Restricted strictly to COMPLETED bookings)
@app.route('/submit_review/<int:vendor_id>', methods=['POST'])
@login_required
@role_required('customer')
def submit_review(vendor_id):
    booking_id = int(request.form.get('booking_id', 0))
    rating = int(request.form['rating'])
    comment = request.form['comment'].strip()
    customer_id = session['user_id']
    
    conn = get_db_connection()
    
    # Check that booking exists, belongs to customer, and is COMPLETED
    booking = conn.execute('''
        SELECT * FROM bookings 
        WHERE booking_id = ? AND customer_id = ? AND vendor_id = ? AND status = 'COMPLETED'
    ''', (booking_id, customer_id, vendor_id)).fetchone()
    
    if not booking:
        conn.close()
        flash('Reviews can only be submitted for COMPLETED bookings!', 'danger')
        return redirect(url_for('profile'))
        
    # Check duplicate review
    existing_review = conn.execute('SELECT * FROM reviews WHERE booking_id = ?', (booking_id,)).fetchone()
    if existing_review:
        conn.close()
        flash('You have already submitted a review for this completed booking.', 'warning')
        return redirect(url_for('profile'))
        
    conn.execute('''
        INSERT INTO reviews (customer_id, vendor_id, booking_id, rating, comment)
        VALUES (?, ?, ?, ?, ?)
    ''', (customer_id, vendor_id, booking_id, rating, comment))
    conn.commit()
    
    log_audit(customer_id, 'REVIEW_SUBMITTED', 'review', vendor_id, f"Rating: {rating} stars")
    conn.close()
    
    flash('Thank you for your rating and review!', 'success')
    return redirect(url_for('profile'))

# Admin Manage Vendor Verification
@app.route('/admin/verify/<int:vendor_id>', methods=['POST'])
@login_required
@role_required('admin')
def verify_vendor(vendor_id):
    action = request.form.get('action', 'approve') # approve, reject, request_changes, suspend, reactivate
    notes = request.form.get('notes', '').strip()
    
    conn = get_db_connection()
    vendor = conn.execute('SELECT v.*, u.email, u.full_name FROM vendors v JOIN users u ON v.user_id = u.user_id WHERE v.vendor_id = ?', (vendor_id,)).fetchone()
    if not vendor:
        conn.close()
        flash('Vendor not found.', 'danger')
        return redirect(url_for('admin_dashboard'))
        
    if action == 'approve' or action == 'reactivate':
        status = 'VERIFIED'
        is_ver = 1
        msg = "Your vendor account has been VERIFIED! Your services are now live on Wedding Utsav."
    elif action == 'reject':
        status = 'REJECTED'
        is_ver = 0
        msg = f"Your vendor verification was REJECTED. Reason: {notes}"
    elif action == 'request_changes':
        status = 'CHANGES_REQUESTED'
        is_ver = 0
        msg = f"Changes requested for your vendor listing. Notes: {notes}"
    elif action == 'suspend':
        status = 'SUSPENDED'
        is_ver = 0
        msg = f"Your vendor account has been SUSPENDED by an administrator. Reason: {notes}"
    else:
        status = vendor['verification_status']
        is_ver = vendor['is_verified']
        msg = ""
        
    conn.execute('''
        UPDATE vendors 
        SET verification_status = ?, is_verified = ?, rejection_reason = ? 
        WHERE vendor_id = ?
    ''', (status, is_ver, notes, vendor_id))
    conn.commit()
    
    log_audit(session['user_id'], f'VENDOR_{action.upper()}', 'vendor', vendor_id, f"New status: {status}. Notes: {notes}")
    send_vendor_status_notification(vendor['email'], vendor['business_name'], msg, notes)
    conn.close()
    
    flash(f"Vendor account updated to {status} successfully!", 'success')
    return redirect(url_for('admin_dashboard'))

# Admin Toggle User Account Status (Active / Suspended)
@app.route('/admin/users/<int:user_id>/toggle_status', methods=['POST'])
@login_required
@role_required('admin')
def toggle_user_status(user_id):
    conn = get_db_connection()
    user = conn.execute('SELECT status FROM users WHERE user_id = ?', (user_id,)).fetchone()
    if not user:
        conn.close()
        flash('User not found.', 'danger')
        return redirect(url_for('admin_dashboard'))
        
    new_status = 'suspended' if user['status'] == 'active' else 'active'
    conn.execute('UPDATE users SET status = ? WHERE user_id = ?', (new_status, user_id))
    conn.commit()
    
    log_audit(session['user_id'], 'USER_STATUS_TOGGLED', 'user', user_id, f"New status: {new_status}")
    conn.close()
    
    flash(f"User account status changed to '{new_status}'.", 'success')
    return redirect(url_for('admin_dashboard'))

# Admin Delete Review
@app.route('/admin/reviews/<int:review_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def delete_review(review_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM reviews WHERE review_id = ?', (review_id,))
    conn.commit()
    log_audit(session['user_id'], 'REVIEW_DELETED', 'review', review_id)
    conn.close()
    
    flash('Review deleted successfully.', 'info')
    return redirect(url_for('admin_dashboard'))

# Help Page
@app.route('/help')
def help_page():
    return render_template('help.html')

if __name__ == '__main__':
    app.run(debug=True)
