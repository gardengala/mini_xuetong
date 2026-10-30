"""迷你学习通 —— 应用入口。"""
import os
import re
import time
import random
import string
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
import click
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.utils import secure_filename
from sqlalchemy import func

from config import Config
from extensions import db, login_manager
from models import User, Course, Enrollment, Schedule, Homework, Submission, SignIn, SignRecord, Notification

app = Flask(__name__)
app.config.from_object(Config)

os.makedirs(os.path.join(os.path.dirname(__file__), 'instance'), exist_ok=True)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)
login_manager.init_app(app)

USERNAME_RE = re.compile(r'^[A-Za-z0-9_]{3,20}$')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$')

def is_safe_url(target):
    if not target: return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def get_user_courses():
    if current_user.is_teacher:
        return Course.query.filter_by(teacher_id=current_user.id).all()
    enrollments = Enrollment.query.filter_by(student_id=current_user.id).all()
    return [e.course for e in enrollments]

# ==================== 认证与首页 ====================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        user = User.query.filter_by(username=username).first()
        if user is None or not user.check_password(password):
            flash('用户名或密码错误。', 'danger')
            return render_template('auth/login.html', username=username), 401
        login_user(user, remember=bool(request.form.get('remember')))
        next_url = request.args.get('next')
        if not next_url or not is_safe_url(next_url): next_url = url_for('index')
        flash(f'欢迎回来，{user.username}！', 'success')
        return redirect(next_url)
    return render_template('auth/login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        role = request.form.get('role') or 'student'
        
        errors = []
        if not USERNAME_RE.match(username): errors.append('用户名格式错误。')
        if len(password) < 6: errors.append('密码少于 6 位。')
        if password != request.form.get('password2'): errors.append('两次密码不一致。')
        if not errors and User.query.filter_by(username=username).first(): errors.append('用户名已存在。')
        
        if errors:
            for msg in errors: flash(msg, 'danger')
            return render_template('auth/register.html', username=username, role=role), 400
            
        user = User(username=username, email=email or None, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash('注册成功，请登录。', 'success')
        return redirect(url_for('login'))
    return render_template('auth/register.html', role='student')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('你已安全退出登录。', 'info')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    current_week = request.args.get('week', 1, type=int)
    if current_week < 1: current_week = 1

    courses = get_user_courses()
    course_ids = [c.id for c in courses]

    schedules = []
    if course_ids:
        schedules = Schedule.query.filter(
            Schedule.course_id.in_(course_ids),
            Schedule.start_week <= current_week,
            Schedule.end_week >= current_week
        ).all()

    schedule_grid = {w: {s: None for s in range(1, 7)} for w in range(1, 8)}
    for sch in schedules:
        if 1 <= sch.weekday <= 7 and 1 <= sch.section <= 6:
            schedule_grid[sch.weekday][sch.section] = sch

    todo_homeworks, active_signs, notices = [], [], []

    if course_ids:
        if current_user.is_teacher:
            todo_homeworks = Homework.query.filter(Homework.course_id.in_(course_ids)).order_by(Homework.create_time.desc()).limit(3).all()
        else:
            my_submissions = Submission.query.filter_by(student_id=current_user.id).with_entities(Submission.homework_id).all()
            submitted_hw_ids = [s.homework_id for s in my_submissions]
            todo_homeworks = Homework.query.filter(
                Homework.course_id.in_(course_ids),
                Homework.deadline >= datetime.now(),
                ~Homework.id.in_(submitted_hw_ids) if submitted_hw_ids else True
            ).order_by(Homework.deadline.asc()).limit(3).all()

            now = datetime.now()
            valid_signs = SignIn.query.filter(
                SignIn.course_id.in_(course_ids),
                SignIn.expire_time > now
            ).all()
            my_sign_ids = [r.sign_id for r in SignRecord.query.filter_by(student_id=current_user.id).all()]
            active_signs = [s for s in valid_signs if s.id not in my_sign_ids]

        notices = Notification.query.filter(Notification.course_id.in_(course_ids)).order_by(Notification.create_time.desc()).limit(3).all()

    return render_template('index.html', current_week=current_week, schedule_grid=schedule_grid, 
                           todo_homeworks=todo_homeworks, active_signs=active_signs, notices=notices)

# ==================== 个人中心 ====================
@app.route('/profile')
@login_required
def profile():
    """个人中心：展示信息、课程与学习进度统计。"""
    courses = get_user_courses()
    course_ids = [c.id for c in courses]
    
    # 1. 作业进度
    total_homeworks = 0
    submitted_count = 0
    if course_ids:
        total_homeworks = Homework.query.filter(Homework.course_id.in_(course_ids)).count()
        if not current_user.is_teacher:
            submitted_count = Submission.query.filter(
                Submission.student_id == current_user.id,
                Submission.homework_id.in_(
                    db.session.query(Homework.id).filter(Homework.course_id.in_(course_ids))
                )
            ).count()

    # 2. 签到进度（出勤率）
    total_signs = 0
    signed_count = 0
    if course_ids:
        total_signs = SignIn.query.filter(SignIn.course_id.in_(course_ids)).count()
        if not current_user.is_teacher:
            signed_count = SignRecord.query.filter_by(student_id=current_user.id).count()

    # 3. 统计出勤率、作业完成率
    attendance_rate = round((signed_count / total_signs * 100), 1) if total_signs > 0 else 0
    homework_rate = round((submitted_count / total_homeworks * 100), 1) if total_homeworks > 0 else 0

    return render_template('profile.html', courses=courses,
                           total_homeworks=total_homeworks, submitted_count=submitted_count, homework_rate=homework_rate,
                           total_signs=total_signs, signed_count=signed_count, attendance_rate=attendance_rate)

# ==================== 课程与课表 ====================
@app.route('/courses')
@login_required
def course_list():
    courses = Course.query.order_by(Course.create_time.desc()).all()
    enrolled_ids = []
    if not current_user.is_teacher:
        enrolled_ids = [e.course_id for e in Enrollment.query.filter_by(student_id=current_user.id).all()]
    return render_template('courses/course_list.html', courses=courses, enrolled_ids=enrolled_ids)

@app.route('/courses/<int:course_id>')
@login_required
def course_detail(course_id):
    course = Course.query.get_or_404(course_id)
    is_owner = (current_user.is_teacher and course.teacher_id == current_user.id)
    notices = Notification.query.filter_by(course_id=course.id).order_by(Notification.create_time.desc()).all()
    
    enrolled_students = []
    if is_owner:
        enrollments = Enrollment.query.filter_by(course_id=course.id).all()
        enrolled_students = [User.query.get(e.student_id) for e in enrollments]
        
    return render_template('courses/course_detail.html', course=course, is_owner=is_owner, notices=notices, enrolled_students=enrolled_students)

@app.route('/teacher/courses/add', methods=['GET', 'POST'])
@login_required
def add_course():
    if not current_user.is_teacher:
        flash('只有教师才能发布课程。', 'warning')
        return redirect(url_for('course_list'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        desc = request.form.get('description', '').strip()
        if not name:
            flash('课程名称不能为空。', 'danger')
        else:
            course = Course(name=name, description=desc, teacher_id=current_user.id)
            db.session.add(course)
            db.session.commit()
            flash('课程发布成功！', 'success')
            return redirect(url_for('course_detail', course_id=course.id))
    return render_template('teacher/add_course.html')

@app.route('/courses/<int:course_id>/enroll', methods=['POST'])
@login_required
def enroll_course(course_id):
    if current_user.is_teacher: return redirect(url_for('course_list'))
    course = Course.query.get_or_404(course_id)
    if Enrollment.query.filter_by(student_id=current_user.id, course_id=course.id).first():
        flash('你已选过该课程。', 'info')
    else:
        db.session.add(Enrollment(student_id=current_user.id, course_id=course.id))
        db.session.commit()
        flash(f'成功选修《{course.name}》！', 'success')
    return redirect(request.referrer or url_for('course_list'))

@app.route('/courses/<int:course_id>/drop', methods=['POST'])
@login_required
def drop_course(course_id):
    if current_user.is_teacher: return redirect(url_for('course_list'))
    enrollment = Enrollment.query.filter_by(student_id=current_user.id, course_id=course_id).first()
    if enrollment:
        db.session.delete(enrollment)
        db.session.commit()
        flash('已退课。', 'info')
    return redirect(request.referrer or url_for('course_list'))

@app.route('/courses/<int:course_id>/schedule/add', methods=['POST'])
@login_required
def add_schedule(course_id):
    course = Course.query.get_or_404(course_id)
    if not current_user.is_teacher or course.teacher_id != current_user.id:
        return redirect(url_for('course_detail', course_id=course.id))

    weekday = request.form.get('weekday', type=int)
    section = request.form.get('section', type=int)
    room = request.form.get('room', '').strip()
    start_week = request.form.get('start_week', type=int)
    end_week = request.form.get('end_week', type=int)

    teacher_course_ids = [c.id for c in Course.query.filter_by(teacher_id=current_user.id).all()]
    conflict = Schedule.query.filter(
        Schedule.course_id.in_(teacher_course_ids),
        Schedule.weekday == weekday,
        Schedule.section == section,
        Schedule.start_week <= end_week,
        Schedule.end_week >= start_week
    ).first()

    if conflict:
        flash(f'排课冲突：你教授的《{conflict.course.name}》已占用了该时间。', 'danger')
    else:
        db.session.add(Schedule(course_id=course.id, weekday=weekday, section=section, room=room, start_week=start_week, end_week=end_week))
        db.session.commit()
        flash('排课成功！', 'success')
    return redirect(url_for('course_detail', course_id=course.id))

# ==================== 作业模块 ====================
@app.route('/courses/<int:course_id>/homeworks', methods=['GET', 'POST'])
@login_required
def homework_list(course_id):
    course = Course.query.get_or_404(course_id)
    is_owner = (current_user.is_teacher and course.teacher_id == current_user.id)
    
    if request.method == 'POST' and is_owner:
        title = request.form.get('title', '').strip()
        desc = request.form.get('description', '').strip()
        deadline_str = request.form.get('deadline')
        try:
            deadline = datetime.strptime(deadline_str, '%Y-%m-%dT%H:%M')
            hw = Homework(course_id=course.id, title=title, description=desc, deadline=deadline)
            db.session.add(hw)
            db.session.commit()
            flash('作业发布成功！', 'success')
        except (ValueError, TypeError):
            flash('截止时间格式错误。', 'danger')
        return redirect(url_for('homework_list', course_id=course.id))

    homeworks = Homework.query.filter_by(course_id=course.id).order_by(Homework.deadline.desc()).all()
    
    submitted_hw_ids = []
    if not current_user.is_teacher:
        subs = Submission.query.filter_by(student_id=current_user.id).all()
        submitted_hw_ids = [s.homework_id for s in subs]

    return render_template('homework/homework_list.html', course=course, homeworks=homeworks, is_owner=is_owner, submitted_hw_ids=submitted_hw_ids)

@app.route('/homework/<int:homework_id>')
@login_required
def homework_detail(homework_id):
    hw = Homework.query.get_or_404(homework_id)
    course = hw.course
    is_owner = (current_user.is_teacher and course.teacher_id == current_user.id)
    
    if is_owner:
        submissions = Submission.query.filter_by(homework_id=hw.id).all()
        return render_template('homework/homework_detail.html', hw=hw, is_owner=True, submissions=submissions)
    else:
        my_sub = Submission.query.filter_by(homework_id=hw.id, student_id=current_user.id).first()
        return render_template('homework/homework_detail.html', hw=hw, is_owner=False, my_sub=my_sub)

@app.route('/homework/<int:homework_id>/submit', methods=['GET', 'POST'])
@login_required
def submit_homework(homework_id):
    if current_user.is_teacher: return redirect(url_for('homework_detail', homework_id=homework_id))
    hw = Homework.query.get_or_404(homework_id)
    
    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        file = request.files.get('file')
        file_db_path = None

        if file and file.filename != '':
            filename = secure_filename(file.filename)
            unique_name = f"{int(time.time())}_{filename}"
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
            file.save(save_path)
            file_db_path = f"uploads/{unique_name}"

        sub = Submission.query.filter_by(homework_id=hw.id, student_id=current_user.id).first()
        if sub:
            sub.content = content
            if file_db_path: sub.file_path = file_db_path
            sub.submit_time = datetime.now()
        else:
            sub = Submission(homework_id=hw.id, student_id=current_user.id, content=content, file_path=file_db_path)
            db.session.add(sub)
            
        db.session.commit()
        flash('作业提交成功！', 'success')
        return redirect(url_for('homework_detail', homework_id=hw.id))

    return render_template('homework/submit.html', hw=hw)

@app.route('/submission/<int:sub_id>/grade', methods=['POST'])
@login_required
def grade_submission(sub_id):
    sub = Submission.query.get_or_404(sub_id)
    if not current_user.is_teacher or sub.homework.course.teacher_id != current_user.id:
        flash('无权操作。', 'danger')
        return redirect(url_for('index'))
    try:
        sub.score = float(request.form.get('score'))
        db.session.commit()
        flash(f'已打分：{sub.score}', 'success')
    except ValueError:
        flash('分数格式错误。', 'danger')
    return redirect(url_for('homework_detail', homework_id=sub.homework_id))

# ==================== 签到与通知 ====================
@app.route('/attendance')
@login_required
def attendance():
    if current_user.is_teacher:
        courses = Course.query.filter_by(teacher_id=current_user.id).all()
        course_ids = [c.id for c in courses]
        signs = SignIn.query.filter(SignIn.course_id.in_(course_ids)).order_by(SignIn.create_time.desc()).all() if course_ids else []
        return render_template('attendance/sign.html', is_teacher=True, courses=courses, signs=signs)
    else:
        records = SignRecord.query.filter_by(student_id=current_user.id).order_by(SignRecord.sign_time.desc()).all()
        return render_template('attendance/sign.html', is_teacher=False, records=records)

@app.route('/attendance/create', methods=['POST'])
@login_required
def create_sign():
    if not current_user.is_teacher: return redirect(url_for('attendance'))
    course_id = request.form.get('course_id', type=int)
    course = Course.query.get(course_id)
    if not course or course.teacher_id != current_user.id:
        flash('课程不存在或无权操作。', 'danger')
        return redirect(url_for('attendance'))
    
    sign_code = ''.join(random.choices(string.digits, k=6))
    minutes = request.form.get('minutes', type=int, default=5)
    expire_time = datetime.now() + timedelta(minutes=minutes)
    
    sign = SignIn(course_id=course.id, sign_code=sign_code, expire_time=expire_time)
    db.session.add(sign)
    db.session.commit()
    flash(f'签到发起成功！签到码：{sign_code}，有效时间 {minutes} 分钟。', 'success')
    return redirect(url_for('attendance'))

@app.route('/attendance/sign', methods=['POST'])
@login_required
def do_sign():
    if current_user.is_teacher: return redirect(url_for('attendance'))
    sign_code = request.form.get('sign_code', '').strip()
    sign = SignIn.query.filter_by(sign_code=sign_code).first()
    
    if not sign:
        flash('签到码错误或不存在。', 'danger')
    elif sign.expire_time < datetime.now():
        flash('该签到码已过期。', 'danger')
    elif not Enrollment.query.filter_by(student_id=current_user.id, course_id=sign.course_id).first():
        flash('你未选修该课程，无需签到。', 'warning')
    elif SignRecord.query.filter_by(sign_id=sign.id, student_id=current_user.id).first():
        flash('你已成功签到过，无需重复操作。', 'info')
    else:
        record = SignRecord(sign_id=sign.id, student_id=current_user.id)
        db.session.add(record)
        db.session.commit()
        flash(f'签到成功！课程：{sign.course.name}', 'success')
        
    return redirect(url_for('attendance'))

@app.route('/attendance/<int:sign_id>')
@login_required
def sign_detail(sign_id):
    sign = SignIn.query.get_or_404(sign_id)
    if not current_user.is_teacher or sign.course.teacher_id != current_user.id:
        flash('无权查看该签到详情。', 'danger')
        return redirect(url_for('attendance'))
        
    enrollments = Enrollment.query.filter_by(course_id=sign.course_id).all()
    all_students = [User.query.get(e.student_id) for e in enrollments]
    signed_student_ids = [r.student_id for r in sign.records]
    
    signed_students = [s for s in all_students if s.id in signed_student_ids]
    unsigned_students = [s for s in all_students if s.id not in signed_student_ids]

    return render_template('attendance/sign_detail.html', sign=sign, 
                           signed_students=signed_students, 
                           unsigned_students=unsigned_students)

@app.route('/courses/<int:course_id>/notice', methods=['POST'])
@login_required
def add_notice(course_id):
    course = Course.query.get_or_404(course_id)
    if not current_user.is_teacher or course.teacher_id != current_user.id:
        return redirect(url_for('course_detail', course_id=course_id))
    
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    if title:
        db.session.add(Notification(course_id=course.id, title=title, content=content))
        db.session.commit()
        flash('通知发布成功！', 'success')
    else:
        flash('通知标题不能为空。', 'danger')
    return redirect(url_for('course_detail', course_id=course_id))

# ==================== CLI ====================
@app.cli.command('init-db')
@click.option('--drop', is_flag=True, help='重建前先删除所有数据表')
def init_db(drop):
    if drop:
        db.drop_all()
        click.echo('已删除旧数据表。')
    db.create_all()
    demo_accounts = [
        {'username': 'student1', 'password': '123456', 'role': 'student', 'email': 'student1@example.com'},
        {'username': 'teacher1', 'password': '123456', 'role': 'teacher', 'email': 'teacher1@example.com'},
    ]
    created = []
    for item in demo_accounts:
        if User.query.filter_by(username=item['username']).first() is None:
            user = User(username=item['username'], role=item['role'], email=item['email'])
            user.set_password(item['password'])
            db.session.add(user)
            created.append(item['username'])
    db.session.commit()
    click.echo('数据库初始化完成：instance/app.db')
    if created:
        click.echo('已创建测试账号：' + '、'.join(created) + '（密码：123456）')

if __name__ == '__main__':
    app.run(debug=True)