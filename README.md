# 💍 Wedding Utsav — Royal Wedding Marketplace Platform

**Wedding Utsav** is a production-grade, Flask-based wedding marketplace application connecting couples with verified local vendors (Venues, Caterers, DJs, and Makeup Artists).

The platform features Role-Based Access Control (RBAC), multi-state vendor verification workflows, a simplified booking pipeline, advance payment tracking, 5% platform commission calculations, double-booking prevention, configurable refund policies, customer soft deletion, system audit logging, and responsive dashboards.

---

## 🌟 Key Features & Architectural Highlights

### 1. 🔐 Role-Based Access Control (RBAC) & Security Hardening
- **Three Core Roles**: `CUSTOMER`, `VENDOR`, `ADMIN`.
- **Backend Authorization**: Enforced strictly on Flask routes using `@role_required(...)` and ownership validation helpers. URL tampering (e.g. customer attempting to access `/admin/dashboard`) triggers styled `403.html` pages or safe redirects.
- **Session Security**: `SESSION_COOKIE_HTTPONLY = True`, `SESSION_COOKIE_SAMESITE = 'Lax'`, and password hashing via `werkzeug.security`.
- **CSRF & Upload Security**: CSRF protection on POST forms and strict file extension validation (`.jpg`, `.jpeg`, `.png`, `.webp`, `.svg`) for profile photos.

### 2. 🛡️ Vendor Verification Workflow
- **Vendor Lifecycle**: `PENDING` → `VERIFIED` / `REJECTED` / `CHANGES_REQUESTED` / `SUSPENDED`.
- **Marketplace Visibility**: Only `VERIFIED` vendors appear in public customer search and receive booking requests.
- **Admin Control**: Admins can approve, reject (with reason notes), request changes, suspend, or reactivate vendor accounts.
- **Backwards Compatibility**: Existing seed vendors are preserved and set to `VERIFIED`, while all newly registered vendors start strictly as `PENDING`.

### 3. 📅 Simplified Booking Lifecycle
```
Customer Requests Booking
       ↓
    PENDING (Awaiting Vendor Acceptance)
       ↓
   ACCEPTED (Vendor Approves Request)
       ↓
PAYMENT_PENDING (Awaiting 20% Advance Payment)
       ↓ [Customer Pays 20% Advance via Sandbox Gateway]
   CONFIRMED (Booking Locked & Confirmed)
       ↓
   COMPLETED (Event Delivered & Revenue Finalized)
```
- **Alternative States**:
  - `PENDING` → `REJECTED` (Vendor declines request)
  - `PENDING` / `ACCEPTED` / `CONFIRMED` → `CANCELLED_BY_CUSTOMER`
  - `CONFIRMED` → `CANCELLED_BY_VENDOR`

### 4. 💰 Financial Price Snapshot, 20% Advance & 5% Platform Revenue Model
- **Immutable Price Snapshot**: When a booking request is submitted, agreed financial values (`original_price`, `discount_amount`, `final_price`, `advance_required`, `commission_amount`, `vendor_earnings`) are locked into the database record. Subsequent price changes by the vendor never alter past bookings.
- **20% Advance Payment**: Calculated from the final agreed booking price (`final_price * 0.20`).
- **5% Platform Commission**: Calculated from the **FINAL agreed booking price** (`final_price * 0.05`), **NOT** from the advance.
- **Vendor Net Earnings**: `final_price - platform_commission`.
- **Financial Calculation Example**:
  - Base Vendor Price: ₹80,000
  - Vendor Discount (10%): ₹8,000
  - Final Agreed Price: ₹72,000
  - Required 20% Advance: ₹14,400
  - Platform Commission (5% of ₹72,000): ₹3,600
  - Net Vendor Earnings: ₹68,400

### 5. 💳 Mock Sandbox Payment Gateway
- Simulates real-world UPI, Credit Card, and NetBanking transactions for advance payments.
- Generates unique transaction reference numbers (`MOCK_TXN_...`).
- Never stores card numbers, CVVs, or sensitive credentials.

### 6. 🔒 Database-Level Double-Booking Prevention & Concurrency Control
- **Database Engine Constraint**: Enforced by a SQLite Partial Unique Index (`idx_active_vendor_booking`) on `bookings(vendor_id, wedding_date)` for active booking statuses (`PENDING`, `ACCEPTED`, `PAYMENT_PENDING`, `CONFIRMED`). If two concurrent requests bypass application logic, SQLite itself raises an `sqlite3.IntegrityError` at the database level.
- **Atomic Transaction Locks**: `create_booking_atomic(...)` acquires an immediate SQLite write lock (`BEGIN IMMEDIATE`) before checking availability and creating booking records.
- **Vendor Availability Blocks**: Re-verifies both active reservations and manually blocked vendor dates (`vendor_availability` table).

### 7. 📜 Cancellation & Refund Policy
- Configurable refund policy helper:
  - **Vendor Cancellation**: 100% full refund of advance paid to customer.
  - **Customer Cancellation**:
    - `PENDING` / `ACCEPTED` / `PAYMENT_PENDING`: No advance paid -> ₹0 refund.
    - `CONFIRMED` (>14 days prior): 100% advance refund.
    - `CONFIRMED` (3-14 days prior): 50% advance refund.
    - `CONFIRMED` (<3 days prior): 0% refund.
- Cancelled booking history is permanently preserved for audit purposes.

### 8. 👤 Customer Soft Deletion
- Customer account deletion updates status to `'deleted'`.
- Anonymizes profile login while preserving historical business, booking, payment, and commission records.

### 9. 📋 Audit Logging
- Tracks all critical platform actions (`USER_REGISTERED`, `USER_LOGIN`, `VENDOR_SETTINGS_UPDATED`, `BOOKING_REQUESTED`, `PAYMENT_SUCCESSFUL`, `BOOKING_CANCELLED`, `ACCOUNT_SOFT_DELETED`, `VENDOR_APPROVED`) in `audit_logs` table with timestamps and IP addresses.

### 10. ⭐ Reviews & Ratings
- Reviews can strictly be submitted ONLY for `COMPLETED` bookings.
- Prevents duplicate reviews per booking.
- Admin moderation capabilities.

---

## 🛠️ Technology Stack

- **Backend**: Python 3, Flask 3.1.1
- **Database**: SQLite3 (`vivaah_vibes.db`) with idempotent schema migrations
- **Templates**: Jinja2 with custom context processors & filters
- **Frontend & Styling**: Responsive HTML5, CSS3, Font Awesome 6, Playfair Display & Outfit Google Fonts
- **Testing**: Python `unittest` suite (`test_suite.py`)

---

## 🚀 How to Run the Application

### 1. Prerequisites
Ensure Python 3.9+ is installed on your system.

### 2. Environment Setup
Create a virtual environment (optional) and install dependencies:
```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` (optional for SMTP configuration):
```bash
cp .env.example .env
```

### 3. Initialize & Seed Database
Run `database.py` to create and migrate all database tables safely:
```bash
python database.py
```

### 4. Start the Application
Run the Flask server:
```bash
python app.py
```
Open your browser and navigate to `http://127.0.0.1:5000`.

---

## 🧪 Running Automated Tests

Run the comprehensive unit test suite covering RBAC, verification, booking lifecycle, double-booking prevention, 20% advance & 5% commission rules, sandbox payments, cancellation refunds, soft delete, and audit logging:

```bash
python test_suite.py
```

---

## 👥 Default Demo Credentials

| Role | Email | Password |
|---|---|---|
| **Admin** | `admin@vivaahvibes.com` | `admin123` |
| **Vendor** | `vendor1@vivaahvibes.com` | `vendor123` |
| **Vendor** | `vendor2@vivaahvibes.com` | `vendor123` |

---

## 📁 Database Structure Overview

- `users`: User accounts (`user_id`, `email`, `password_hash`, `role`, `status`, `city`, `profile_pic_url`, `created_at`)
- `vendors`: Vendor profiles (`vendor_id`, `user_id`, `business_name`, `service_type`, `base_price`, `discount_type`, `discount_value`, `verification_status`, `is_verified`)
- `bookings`: Booking lifecycle & financial snapshots (`booking_id`, `customer_id`, `vendor_id`, `wedding_date`, `original_price`, `discount_amount`, `final_price`, `advance_required`, `advance_paid`, `remaining_amount`, `commission_rate`, `commission_amount`, `vendor_earnings`, `status`, `refund_amount`)
- `vendor_availability`: Vendor manually blocked dates (`availability_id`, `vendor_id`, `blocked_date`, `reason`)
- `payments`: Transaction ledger (`payment_id`, `booking_id`, `customer_id`, `amount`, `payment_type`, `status`, `transaction_reference`, `paid_at`)
- `commissions`: Platform 5% revenue records (`commission_id`, `booking_id`, `vendor_id`, `gross_amount`, `commission_rate`, `commission_amount`, `vendor_earnings`, `status`)
- `reviews`: Completed booking ratings (`review_id`, `customer_id`, `vendor_id`, `booking_id`, `rating`, `comment`, `created_at`)
- `budgets`: Customer budget planner (`budget_id`, `customer_id`, `total_budget`, `venue_allocated`, `catering_allocated`, `dj_allocated`, `makeup_allocated`)
- `wedding_rituals`: Event timeline dates (`ritual_id`, `customer_id`, `haldi_date`, `mehendi_date`, `sangeet_date`, `wedding_date`)
- `audit_logs`: Activity log audit trail (`log_id`, `user_id`, `action`, `entity_type`, `entity_id`, `details`, `ip_address`, `timestamp`)
