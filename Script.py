import telebot
from telebot import types
import mysql.connector
import logging
from datetime import datetime
import secrets
import string
import re
from telegram.ext import ApplicationBuilder, CommandHandler
from config import BOT_TOKEN



# تهيئة اللوق
logging.basicConfig(level=logging.INFO)

# التوكن
bot = telebot.TeleBot('8049243832:AAEMPPGW8S5KodOQl-sOO1Dc0q8LHaNxeks')

# الاتصال بقاعدة البيانات
try:
    conn = mysql.connector.connect(
        host='localhost',
        user='root',
        password='',
        database='complaints_db'
    )
    cursor = conn.cursor(buffered=True)
    logging.info("✅ تم الاتصال بقاعدة البيانات بنجاح")
except mysql.connector.Error as err:
    logging.error(f"❌ خطأ في الاتصال بقاعدة البيانات: {err}")
    raise

# الثوابت والمتغيرات العامة
ROLES = {
    'employee': 'موظف',
    'technician': 'مهندس/ فني',
    'manager': 'مدير'
}

ALLOWED_STATUSES = ['قيد العمل', 'تم التنفيذ', 'مرفوض', 'بانتظار قطع الغيار', 'معلقة', 'قيد التنفيذ']

ISSUE_TYPES = {
    "pc_issue": "💻 مشاكل الحاسوب",
    "printer_issue": "🖨 مشاكل الطابعة",
    "internet_issue": "🌐 مشاكل الإنترنت",
    "screen_issue": "🖥 مشاكل الشاشة",
    "keyboard_mouse": "⌨ الكيبورد/الماوس",
    "login_issue": "🔐 مشاكل الدخول",
    "software_issue": "🧾 مشاكل برمجية",
    "vpn_issue": "📡 الشبكة الداخلية/VPN",
    "maintenance": "🛠 طلب صيانة",
    "device_request": "📦 طلب جهاز جديد",
    "other_issue": "❓ أخرى"
}

user_selected_category = {}
user_temp_data = {}

# --- الدوال المساعدة ---
def has_role(user_id, required_role):
    """تحقق إذا كان المستخدم لديه الدور المطلوب"""
    cursor.execute("SELECT role FROM user_roles WHERE user_id = %s", (user_id,))
    roles = [r[0] for r in cursor.fetchall()]
    return required_role in roles

def get_complaint_duration(created_at, end_time):
    """حساب مدة الشكوى"""
    if not created_at or not isinstance(created_at, datetime):
        return "غير محسوبة"
    
    end = end_time if end_time else datetime.now()
    total_seconds = int((end - created_at).total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours} ساعة و {minutes} دقيقة" if hours > 0 else f"{minutes} دقيقة فقط"
# دوال مساعدة جديدة
def create_general_employee_code():
    """إنشاء رمز دعوة عام للموظفين"""
    try:
        # رمز ثابت للموظفين
        general_code = "EMPLOYEE2025"
        
        # التحقق إذا كان الرمز موجوداً بالفعل
        cursor.execute("SELECT id FROM invitation_codes WHERE code = %s", (general_code,))
        if not cursor.fetchone():
            from datetime import datetime, timedelta
            expires_at = datetime.now() + timedelta(days=365)  # صلاحية سنة كاملة
            
            cursor.execute("""
                INSERT INTO invitation_codes (code, role, created_by, expires_at, notes)
                VALUES (%s, %s, %s, %s, %s)
            """, (general_code, 'employee', 0, expires_at, 'رمز عام للموظفين'))
            conn.commit()
            
        return general_code
    except Exception as e:
        logging.error(f"Error creating general employee code: {e}")
        return None

def is_general_employee_code(code):
    """التحقق إذا كان الرمز هو الرمز العام للموظفين"""
    return code == "EMPLOYEE2025"

# --- نظام التسجيل الآمن ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    cursor.execute("SELECT name FROM users WHERE user_id = %s", (user_id,))
    user = cursor.fetchone()

    if user:
        cursor.execute("SELECT role FROM user_roles WHERE user_id = %s", (user_id,))
        roles = [r[0] for r in cursor.fetchall()]
        roles_text = ', '.join([ROLES.get(r, r) for r in roles])
        bot.reply_to(message, f"مرحباً بك مرة أخرى! أدوارك: {roles_text}")
    else:
        # إنشاء الرمز العام للموظفين إذا لم يكن موجوداً
        create_general_employee_code()
        
        # مستخدم جديد - لا نعرض عليه خيار الأدوار
        msg = bot.reply_to(message, "🔐 نظام التسجيل الشكاوي\n\n"
                                  "للتسجيل في النظام، تحتاج إلى رمز دعوة.\n\n"
                                  "🔸 للموظفين: الرمز العام هو `EMPLOYEE2025`\n"
                                  "🔸 للفنيين/المديرين: تواصل مع المدير\n\n"
                                  "الرجاء إدخال رمز الدعوة الآن:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_invite_code)

def process_invite_code(message):
    user_id = message.from_user.id
    invite_code = message.text.strip()
    
    # التحقق من الرمز العام للموظفين أولاً
    if is_general_employee_code(invite_code):
        # تخزين البيانات مؤقتاً كموظف
        user_temp_data[user_id] = {'role': 'employee', 'invite_code': invite_code}
        
        # طلب المعلومات الشخصية
        msg = bot.reply_to(message, "الرجاء إدخال اسمك الكامل:")
        bot.register_next_step_handler(msg, process_name_with_invite)
        return
    
    # التحقق من صحة رمز الدعوة العادي
    cursor.execute("""
        SELECT role, expires_at, used 
        FROM invitation_codes 
        WHERE code = %s AND expires_at > NOW() AND used = FALSE
    """, (invite_code,))
    
    code_data = cursor.fetchone()
    
    if not code_data:
        bot.reply_to(message, "❌ رمز الدعوة غير صالح أو منتهي الصلاحية أو مستخدم مسبقاً.")
        return
    
    role, expires_at, used = code_data
    
    # تخزين البيانات مؤقتاً
    user_temp_data[user_id] = {'role': role, 'invite_code': invite_code}
    
    # طلب المعلومات الشخصية
    msg = bot.reply_to(message, "الرجاء إدخال اسمك الكامل:")
    bot.register_next_step_handler(msg, process_name_with_invite)

def process_name_with_invite(message):
    user_id = message.from_user.id
    name = message.text
    
    if user_id not in user_temp_data:
        bot.reply_to(message, "❌ انتهت الجلسة، يرجى البدء من جديد باستخدام /start")
        return
        
    user_temp_data[user_id]['name'] = name
    
    msg = bot.reply_to(message, "الرجاء إدخال رقم هاتفك:")
    bot.register_next_step_handler(msg, process_phone_with_invite)

def process_phone_with_invite(message):
    user_id = message.from_user.id
    phone = message.text
    
    if user_id not in user_temp_data:
        bot.reply_to(message, "❌ انتهت الجلسة، يرجى البدء من جديد باستخدام /start")
        return
        
    role = user_temp_data[user_id]['role']
    invite_code = user_temp_data[user_id]['invite_code']
    name = user_temp_data[user_id]['name']
    
    try:
        # حفظ المستخدم في قاعدة البيانات
        cursor.execute("INSERT INTO users (user_id, name, phone) VALUES (%s, %s, %s)", 
                      (user_id, name, phone))
        
        # تعيين الدور
        cursor.execute("INSERT INTO user_roles (user_id, role) VALUES (%s, %s)", (user_id, role))
        
        # إذا لم يكن رمزاً عاماً،标记 الرمز كمستخدم
        if not is_general_employee_code(invite_code):
            cursor.execute("UPDATE invitation_codes SET used = TRUE, used_by = %s, used_at = NOW() WHERE code = %s", 
                          (user_id, invite_code))
        
        conn.commit()
        
        # تنظيف البيانات المؤقتة
        if user_id in user_temp_data:
            del user_temp_data[user_id]
            
        bot.send_message(message.chat.id, f"✅ تم تسجيلك بنجاح كـ {ROLES[role]}.")
        
    except Exception as e:
        logging.error(f"Error completing registration: {e}")
        bot.reply_to(message, "❌ حدث خطأ أثناء إكمال التسجيل.")
@bot.message_handler(commands=['show_general_code'])
def show_general_code(message):
    if not has_role(message.from_user.id, 'manager'):
        bot.reply_to(message, "❌ هذا الأمر مخصص للمدير فقط.")
        return
    
    general_code = create_general_employee_code()
    if general_code:
        bot.reply_to(message, f"🔑 الرمز العام للموظفين: `{general_code}`\n\n"
                             "يمكن للموظفين استخدام هذا الرمز للتسجيل كموظفين.\n"
                             "الرمز صالح لمدة سنة من الآن.", parse_mode="Markdown")
    else:
        bot.reply_to(message, "❌ حدث خطأ أثناء إنشاء الرمز العام.")

# --- أوامر المدير لإدارة الدعوات ---
@bot.message_handler(commands=['create_invite'])
def create_invite_code(message):
    if not has_role(message.from_user.id, 'manager'):
        bot.reply_to(message, "❌ هذا الأمر مخصص للمدير فقط.")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "❗ الاستخدام: /create_invite <دور> [مدة_بالأيام]")
        return
    
    role = parts[1]
    if role not in ['employee', 'technician', 'manager']:
        bot.reply_to(message, "❌ دور غير صحيح. الأدوار المتاحة: employee, technician, manager")
        return
    
    # إنشاء رمز دعوة فريد
    code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    
    # تحديد مدة الصلاحية (افتراضي 7 أيام)
    expires_days = int(parts[2]) if len(parts) > 2 else 7
    
    try:
        from datetime import datetime, timedelta
        expires_at = datetime.now() + timedelta(days=expires_days)
        
        cursor.execute("""
            INSERT INTO invitation_codes (code, role, created_by, expires_at)
            VALUES (%s, %s, %s, %s)
        """, (code, role, message.from_user.id, expires_at))
        conn.commit()
        
        bot.reply_to(message, f"✅ تم إنشاء رمز دعوة للدور '{ROLES[role]}'.\n\n"
                             f"🔑 الرمز: `{code}`\n"
                             f"⏰ ينتهي في: {expires_at.strftime('%Y-%m-%d %H:%M')}\n\n"
                             f"شارك هذا الرمز مع المستخدم المدعو.", parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Error creating invite code: {e}")
        bot.reply_to(message, "❌ حدث خطأ أثناء إنشاء رمز الدعوة.")

@bot.message_handler(commands=['list_invites'])
def list_invite_codes(message):
    if not has_role(message.from_user.id, 'manager'):
        bot.reply_to(message, "❌ هذا الأمر مخصص للمدير فقط.")
        return
    
    cursor.execute("""
        SELECT code, role, created_at, expires_at, used, used_by
        FROM invitation_codes 
        WHERE created_by = %s
        ORDER BY created_at DESC
    """, (message.from_user.id,))
    
    codes = cursor.fetchall()
    
    if not codes:
        bot.reply_to(message, "❌ لم تنشئ أي رموز دعوة بعد.")
        return
    
    response = "📋 رموز الدعوة التي أنشأتها:\n\n"
    for code, role, created_at, expires_at, used, used_by in codes:
        status = "✅ مستخدم" if used else "🆕 غير مستخدم"
        response += f"🔸 {code} - {ROLES[role]} - {status}\n"
        response += f"   ⏰ ينتهي: {expires_at.strftime('%Y-%m-%d')}\n\n"
    
    bot.reply_to(message, response)

# --- معالجة الشكاوى ---
def get_title_step(message):
    if message.text.startswith('/'):
        bot.reply_to(message, "❌ تم إلغاء الشكوى لأنك أرسلت أمرًا وليس عنوانًا.")
        return
    
    title = message.text
    msg = bot.reply_to(message, "📝 الرجاء إدخال وصف الشكوى بشكل مفصل:")
    bot.register_next_step_handler(msg, lambda msg: get_description_step(msg, title))

def get_description_step(message, title):
    if message.text.startswith('/'):
        bot.reply_to(message, "❌ تم إلغاء الشكوى لأنك أرسلت أمرًا.")
        return
    
    description = message.text
    msg = bot.reply_to(message, "🏢 الرجاء إدخال رقم الغرفة:")
    bot.register_next_step_handler(msg, lambda msg: save_complaint(msg, title, description))

def save_complaint(message, title, description):
    try:
        room_number = message.text
        user_id = message.from_user.id
        issue_type = user_selected_category.get(user_id, "other_issue")
        
        logging.info(f"محاولة حفظ شكوى: user_id={user_id}, title={title}, room={room_number}")
        
        # التحقق من أن المستخدم مسجل في النظام
        cursor.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,))
        if not cursor.fetchone():
            bot.reply_to(message, "❌ يجب أن تكون مسجلاً في النظام لتقديم شكوى.")
            return
        
        cursor.execute("""
            INSERT INTO complaints (user_id, title, description, room_number, status, issue_type)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, title, description, room_number, 'معلقة', ISSUE_TYPES.get(issue_type, "other_issue")))
        conn.commit()
        
        complaint_id = cursor.lastrowid
        
        # حذف التصنيف المؤقت إذا exists
        if user_id in user_selected_category:
            del user_selected_category[user_id]
            
        logging.info(f"تم حفظ الشكوى بنجاح: complaint_id={complaint_id}")
        bot.reply_to(message, f"✅ تم استلام شكواك بنجاح!\n🔢 رقم الشكوى: {complaint_id}")
        
    except mysql.connector.Error as err:
        logging.error(f"Database error saving complaint: {err}")
        bot.reply_to(message, "❌ حدث خطأ في قاعدة البيانات أثناء حفظ الشكوى.")
    except Exception as e:
        logging.error(f"Error saving complaint: {e}")
        bot.reply_to(message, "❌ حدث خطأ غير متوقع أثناء حفظ الشكوى.")

@bot.message_handler(commands=['complaint'])
def start_complaint(message):
    user_id = message.from_user.id
    cursor.execute("SELECT role FROM user_roles WHERE user_id = %s", (user_id,))
    roles = [r[0] for r in cursor.fetchall()]
    
    if not roles:
        bot.reply_to(message, "❌ يجب أن تكون مسجلاً في النظام لتقديم شكوى.")
        return
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton(text, callback_data=f"complaint_category:{key}") 
               for key, text in ISSUE_TYPES.items()]
    keyboard.add(*buttons)
    bot.send_message(message.chat.id, "🗂 الرجاء اختيار نوع المشكلة:", reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith("complaint_category:"))
def handle_complaint_category(call):
    category_key = call.data.split(":")[1]
    selected_label = ISSUE_TYPES.get(category_key, "❓ أخرى")
    user_selected_category[call.from_user.id] = category_key
    bot.answer_callback_query(call.id)
    
    msg = bot.send_message(call.message.chat.id, f"✅ اخترت: *{selected_label}*\n\n📝 الرجاء إدخال **عنوان مختصر للشكوى**:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, get_title_step)

@bot.message_handler(commands=['all_complaints'])
def all_complaints(message):
    if not has_role(message.from_user.id, 'manager'):
        bot.reply_to(message, "❌ هذا الأمر مخصص للمدير فقط.")
        return

    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton(text.split()[-1], callback_data=f"filter_type:{key}") 
               for key, text in ISSUE_TYPES.items()]
    markup.add(*buttons)
    markup.add(types.InlineKeyboardButton("📋 الكل", callback_data="filter_type:all"))
    bot.send_message(message.chat.id, "🔎 اختر نوع المشكلة لعرض الشكاوى:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("filter_type:"))
def filter_complaints_by_type(call):
    issue_type = call.data.split(":")[1]
    filter_type = issue_type if issue_type != 'all' else None

    query = """
        SELECT c.complaint_id, u.name, c.title, c.description, c.status, c.issue_type
        FROM complaints c
        JOIN users u ON c.user_id = u.user_id
    """
    params = ()
    
    if filter_type:
        query += " WHERE c.issue_type = %s"
        params = (ISSUE_TYPES.get(filter_type),)

    query += " ORDER BY c.created_at DESC"
    cursor.execute(query, params)
    complaints = cursor.fetchall()

    if complaints:
        response = f"📋 نتائج الفلترة ({ISSUE_TYPES.get(issue_type, 'الكل')}):\n\n"
        for c in complaints:
            response += (
                f"🔹 رقم: {c[0]}\n"
                f"👤 مقدم الشكوى: {c[1]}\n"
                f"📌 النوع: {c[5] or 'غير محدد'}\n"
                f"🔄 الحالة: {c[4]}\n"
                f"📝 العنوان: {c[2]}\n"
                "------------------------\n"
            )
        bot.send_message(call.message.chat.id, response)
    else:
        bot.send_message(call.message.chat.id, "⚠️ لا توجد شكاوى مطابقة للفلترة المطلوبة")

    bot.answer_callback_query(call.id)

@bot.message_handler(commands=['assign', 'assign_to'])
def assign_complaint_interactive(message):
    if not has_role(message.from_user.id, 'manager'):
        bot.reply_to(message, "❌ هذا الأمر مخصص للمدير فقط.")
        return

    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "❗ الرجاء إدخال رقم الشكوى: /assign_to <رقم_الشكوى>")
        return

    complaint_id = parts[1]
    cursor.execute("""
        SELECT u.user_id, u.name FROM users u
        JOIN user_roles r ON u.user_id = r.user_id
        WHERE r.role = 'technician'
    """)
    technicians = cursor.fetchall()

    if not technicians:
        bot.send_message(message.chat.id, "❌ لا يوجد فنيين حالياً.")
        return

    markup = types.InlineKeyboardMarkup()
    for tech_id, name in technicians:
        markup.add(types.InlineKeyboardButton(text=name, callback_data=f"assign:{complaint_id}:{tech_id}"))

    bot.send_message(message.chat.id, f"👨‍🔧 اختر الفني لتعيين الشكوى رقم {complaint_id}:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('assign:'))
def handle_assign_callback(call):
    try:
        _, complaint_id, technician_id = call.data.split(':')
        user_id = call.from_user.id

        cursor.execute("""
            UPDATE complaints
            SET assigned_to = %s, status = 'قيد التنفيذ'
            WHERE complaint_id = %s
        """, (technician_id, complaint_id))
        
        cursor.execute("""
            INSERT INTO complaint_logs (complaint_id, action_by, action_type, notes, timestamp)
            VALUES (%s, %s, %s, %s, NOW())
        """, (complaint_id, user_id, 'تعيين مهندس/ فني', f"تم تعيين مهندس/ فني {technician_id}"))
        conn.commit()

        bot.send_message(technician_id, f"📬 تم تعيين شكوى جديدة إليك. رقم الشكوى: {complaint_id}")
        bot.edit_message_text(chat_id=call.message.chat.id,
                            message_id=call.message.message_id,
                            text=f"✅ تم تعيين الشكوى رقم {complaint_id} للفني بنجاح.")
    except Exception as e:
        logging.error(e)
        bot.answer_callback_query(call.id, "❌ حدث خطأ أثناء تعيين الشكوى.")

@bot.message_handler(commands=['my_complaints'])
def my_complaints(message):
    user_id = message.from_user.id
    cursor.execute("""
        SELECT c.complaint_id, c.title, c.description, c.status, c.issue_type
        FROM complaints c
        WHERE c.user_id = %s
        ORDER BY c.created_at DESC
    """, (user_id,))
    
    complaints = cursor.fetchall()

    if complaints:
        response = "📜 شكاويك المقدمة:\n\n"
        for c in complaints:
            response += (
                f"🔸 رقم الشكوى: {c[0]}\n"
                f"📌 النوع: {c[4] or 'غير محدد'}\n"
                f"🔄 الحالة: {c[3]}\n"
                f"📝 العنوان: {c[1]}\n"
                "------------------------\n"
            )
        bot.send_message(message.chat.id, response)
    else:
        bot.send_message(message.chat.id, "⚠️ لم تقم بتقديم أي شكاوى حتى الآن")

@bot.message_handler(commands=['update'])
def update_complaint_status(message):
    if not has_role(message.from_user.id, 'technician'):
        bot.reply_to(message, "❌ هذا الأمر مخصص مهندس/ فني فقط.")
        return

    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "❗ الاستخدام الصحيح: /update <رقم الشكوى> <الحالة>")
        return

    try:
        complaint_id = int(parts[1])
        new_status = ' '.join(parts[2:])

        if new_status not in ALLOWED_STATUSES:
            bot.reply_to(message, f"❌ الحالة غير معروفة. الحالات المسموحة هي:\n- " + "\n- ".join(ALLOWED_STATUSES))
            return

        cursor.execute("SELECT * FROM complaints WHERE complaint_id = %s AND assigned_to = %s", 
                      (complaint_id, message.from_user.id))
        if not cursor.fetchone():
         if not cursor.fetchone():
            bot.reply_to(message, "❌ هذه الشكوى غير معينة لك أو غير موجودة.")
            return

        # تحديث حالة الشكوى
        query = """UPDATE complaints SET status = %s"""
        params = [new_status]
        
        if new_status == 'تم التنفيذ':
            query += ", end_time = NOW()"
            
        query += " WHERE complaint_id = %s"
        params.append(complaint_id)
        
        cursor.execute(query, params)
        
        # تسجيل التغيير في السجل
        cursor.execute("""
            INSERT INTO complaint_logs (complaint_id, action_by, action_type, notes, timestamp)
            VALUES (%s, %s, %s, %s, NOW())
        """, (complaint_id, message.from_user.id, 'تحديث الحالة', f"تم تغيير الحالة إلى: {new_status}"))
        conn.commit()

        bot.reply_to(message, f"✅ تم تحديث حالة الشكوى رقم {complaint_id} إلى: {new_status}")

    except Exception as e:
        logging.error(e)
        bot.reply_to(message, "❌ حدث خطأ أثناء تحديث الحالة.")

@bot.message_handler(commands=['track'])
def track_complaint(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "❗ الرجاء إدخال رقم الشكوى: /track <رقم_الشكوى>")
        return

    complaint_id = parts[1]

    try:
        # جلب معلومات الشكوى الأساسية
        cursor.execute("""
            SELECT c.complaint_id, c.title, c.description, c.status, c.assigned_to, 
                   c.created_at, c.end_time, c.room_number, u.name, c.issue_type
            FROM complaints c
            LEFT JOIN users u ON c.assigned_to = u.user_id
            WHERE c.complaint_id = %s
        """, (complaint_id,))
        complaint = cursor.fetchone()

        if not complaint:
            bot.reply_to(message, "❌ لا يوجد سجل لهذه الشكوى أو الشكوى غير موجودة.")
            return

        # جلب سجل التتبع
        cursor.execute("""
            SELECT cl.action_type, cl.notes, cl.timestamp, u.name
            FROM complaint_logs cl
            JOIN users u ON cl.action_by = u.user_id
            WHERE cl.complaint_id = %s
            ORDER BY cl.timestamp
        """, (complaint_id,))
        logs = cursor.fetchall()

        # بناء الرد
        reply = f"📍 تفاصيل الشكوى رقم {complaint[0]}:\n"
        reply += f"📌 النوع: {complaint[9] or 'غير محدد'}\n"
        reply += f"📝 العنوان: {complaint[1]}\n"
        reply += f"📄 الوصف: {complaint[2]}\n"
        reply += f"🚩 الحالة: {complaint[3]}\n"
        reply += f"👤 مهندس/ فني المسؤول: {complaint[8] if complaint[8] else 'غير معين'}\n"
        reply += f"🕒 تاريخ الإنشاء: {complaint[5].strftime('%Y-%m-%d %H:%M') if complaint[5] else 'غير معروف'}\n"
        reply += f"🕓 تاريخ الانتهاء: {(complaint[6].strftime('%Y-%m-%d %H:%M') if complaint[6] else 'غير منتهية')}\n"
        reply += f"📍 رقم الغرفة: {complaint[7] if complaint[7] else 'غير محدد'}\n"
        reply += f"⏳ مدة الشكوى: {get_complaint_duration(complaint[5], complaint[6])}\n\n"

        reply += "🗂 سجل التتبع:\n"
        if logs:
            for action, notes, ts, username in logs:
                timestamp_str = ts.strftime('%Y-%m-%d %H:%M') if hasattr(ts, 'strftime') else str(ts)
                reply += f"🔹 {timestamp_str} - {username} قام بـ [{action}]: {notes}\n"
        else:
            reply += "لا توجد تغييرات مسجلة على الشكوى حتى الآن."

        bot.send_message(message.chat.id, reply)

    except Exception as e:
        logging.error(e)
        bot.reply_to(message, "❌ حدث خطأ أثناء جلب تفاصيل الشكوى.")

@bot.message_handler(commands=['stats'])
def stats_handler(message):
    try:
        # إحصائيات أساسية
        cursor.execute("SELECT COUNT(*) FROM complaints")
        total_complaints = cursor.fetchone()[0]

        cursor.execute("SELECT status, COUNT(*) FROM complaints GROUP BY status")
        status_counts = cursor.fetchall()

        # إحصائيات الفنيين
        cursor.execute("""
            SELECT u.name, COUNT(*) AS total_assigned
            FROM complaints c
            JOIN users u ON c.assigned_to = u.user_id
            GROUP BY u.name
        """)
        assigned_counts = cursor.fetchall()

        cursor.execute("""
            SELECT u.name, COUNT(*) AS completed_count
            FROM complaints c
            JOIN users u ON c.assigned_to = u.user_id
            WHERE c.status IN ('تم التنفيذ', 'مغلقة', 'تم الانتهاء')
            GROUP BY u.name
        """)
        completed_counts = cursor.fetchall()

        # متوسط وقت الحل
        cursor.execute("""
            SELECT AVG(TIMESTAMPDIFF(SECOND, created_at, end_time)) 
            FROM complaints 
            WHERE end_time IS NOT NULL
        """)
        avg_seconds = cursor.fetchone()[0]
        avg_time_str = f"{avg_seconds / 3600:.2f} ساعة" if avg_seconds else "لا توجد بيانات كافية"

        # بناء الرسالة
        msg = f"📊 إحصائيات الشكاوى:\n\n"
        msg += f"🔹 إجمالي الشكاوى: {total_complaints}\n\n"
        msg += "🔹 عدد الشكاوى حسب الحالة:\n"
        msg += "\n".join(f"   - {status}: {count}" for status, count in status_counts)
        
        msg += "\n\n🔹 عدد الشكاوى المخصصة لكل فني/مهندس:\n"
        msg += "\n".join(f"   - {name}: {count}" for name, count in assigned_counts) if assigned_counts else "   لا توجد شكاوى مخصصة."
        
        msg += "\n\n🔹 عدد الشكاوى المنجزة لكل فني/مهندس:\n"
        msg += "\n".join(f"   - {name}: {count}" for name, count in completed_counts) if completed_counts else "   لا توجد شكاوى منجزة."
        
        msg += f"\n\n🔹 المتوسط الزمني لحل الشكاوى: {avg_time_str}"

        bot.reply_to(message, msg)

    except Exception as e:
        logging.error(f"Error in /stats: {e}")
        bot.reply_to(message, "❌ حدث خطأ أثناء جلب الإحصائيات.")

@bot.message_handler(commands=['help'])
def show_help(message):
    user_id = message.from_user.id
    cursor.execute("SELECT role FROM user_roles WHERE user_id = %s", (user_id,))
    roles = [r[0] for r in cursor.fetchall()]

    help_text = "📋 قائمة الأوامر المتاحة:\n\n"
    help_text += "/start - بدء التفاعل مع البوت\n"
    help_text += "/complaint - تقديم شكوى جديدة\n"
    help_text += "/track <رقم_الشكوى> - تتبع الشكوى\n"

    if 'employee' in roles:
        help_text += "/my_complaints - عرض شكاواي\n"

    if 'technician' in roles:
        help_text += "/my_complaints - الشكاوى المسندة إليّ\n"
        help_text += "/update <رقم_الشكوى> <الحالة> - تحديث حالة شكوى\n"

    if 'manager' in roles:
        help_text += "/create_invite - إنشاء رمز دعوة\n"
        help_text += "/list_invites - عرض رموز الدعوة\n"
        help_text += "/assign <رقم_الشكوى> <رقم_الفني> - تعيين فني لشكوى\n"
        help_text += "/all_complaints - عرض جميع الشكاوى\n"
        help_text += "/stats - إحصائيات النظام\n"

    bot.send_message(message.chat.id, help_text)

@bot.message_handler(commands=['refresh_roles'])
def refresh_roles(message):
    user_id = message.from_user.id
    
    # التحقق من الصلاحيات مباشرة من قاعدة البيانات (بدون الاعتماد على الذاكرة)
    cursor.execute("SELECT role FROM user_roles WHERE user_id = %s", (user_id,))
    roles = [r[0] for r in cursor.fetchall()]
    
    if not roles:
        bot.reply_to(message, "⚠️ لا تملك أي صلاحيات حالياً. يمكنك التسجيل باستخدام /start")
    else:
        roles_text = ', '.join([ROLES.get(r, r) for r in roles])
        bot.reply_to(message, f"✅ تم تحديث صلاحياتك. أدوارك الحالية: {roles_text}")

@bot.message_handler(commands=['check_db'])
def check_db_status(message):
    try:
        # التحقق من اتصال قاعدة البيانات
        cursor.execute("SELECT 1")
        db_connection = "✅ متصل"
    except:
        db_connection = "❌ غير متصل"
    
    try:
        # التحقق من وجود جدول الشكاوى
        cursor.execute("SHOW TABLES LIKE 'complaints'")
        complaints_table = "✅ موجود" if cursor.fetchone() else "❌ غير موجود"
    except:
        complaints_table = "❌ خطأ في التحقق"
    
    try:
        # عدد الشكاوى في النظام
        cursor.execute("SELECT COUNT(*) FROM complaints")
        complaints_count = cursor.fetchone()[0]
    except:
        complaints_count = "❌ خطأ في العد"
    
    response = f"""
📊 حالة النظام:
    
🔌 قاعدة البيانات: {db_connection}
📋 جدول الشكاوى: {complaints_table}
🔢 عدد الشكاوى: {complaints_count}
"""
    bot.reply_to(message, response)
async def start(update, context):
    await update.message.reply_text("أهلاً 👋 البوت شغال!")

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))

# تعديل للكوميت المستقل


# التشغيل الرئيسي
if __name__ == "__main__":
    logging.info("🤖 البوت يعمل بنجاح  ")
    bot.infinity_polling()
    app.run_polling()