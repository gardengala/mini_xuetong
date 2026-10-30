"""数据模型定义。"""
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash
from extensions import db, login_manager

class User(UserMixin, db.Model):
    """用户模型。"""
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False, default='student')
    email = db.Column(db.String(120), unique=True, nullable=True)
    create_time = db.Column(db.DateTime, nullable=False, default=datetime.now)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    @property
    def is_teacher(self):
        return self.role == 'teacher'

    @property
    def role_label(self):
        return '教师' if self.role == 'teacher' else '学生'

class Course(db.Model):
    """课程模型。"""
    __tablename__ = 'course'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.now)

    teacher = db.relationship('User', backref='taught_courses')
    schedules = db.relationship('Schedule', backref='course', cascade='all, delete-orphan')
    enrollments = db.relationship('Enrollment', backref='course', cascade='all, delete-orphan')
    homeworks = db.relationship('Homework', backref='course', cascade='all, delete-orphan')
    sign_ins = db.relationship('SignIn', backref='course', cascade='all, delete-orphan')
    notifications = db.relationship('Notification', backref='course', cascade='all, delete-orphan')

class Enrollment(db.Model):
    """选课表。"""
    __tablename__ = 'enrollment'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('student_id', 'course_id', name='_student_course_uc'),)

class Schedule(db.Model):
    """课表项。"""
    __tablename__ = 'schedule'
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    weekday = db.Column(db.Integer, nullable=False)
    section = db.Column(db.Integer, nullable=False)
    room = db.Column(db.String(50))
    start_week = db.Column(db.Integer, nullable=False, default=1)
    end_week = db.Column(db.Integer, nullable=False, default=16)

class Homework(db.Model):
    """作业模型。"""
    __tablename__ = 'homework'
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    deadline = db.Column(db.DateTime, nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.now)

class Submission(db.Model):
    """作业提交记录。"""
    __tablename__ = 'submission'
    id = db.Column(db.Integer, primary_key=True)
    homework_id = db.Column(db.Integer, db.ForeignKey('homework.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text)
    file_path = db.Column(db.String(255))
    score = db.Column(db.Float, nullable=True)
    submit_time = db.Column(db.DateTime, default=datetime.now)

    homework = db.relationship('Homework', backref=db.backref('submissions', cascade='all, delete-orphan'))
    student = db.relationship('User', backref='submissions')
    __table_args__ = (db.UniqueConstraint('homework_id', 'student_id', name='_homework_student_uc'),)

class SignIn(db.Model):
    """签到活动模型。"""
    __tablename__ = 'sign_in'
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    sign_code = db.Column(db.String(6), nullable=False, index=True)
    expire_time = db.Column(db.DateTime, nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.now)

    records = db.relationship('SignRecord', backref='sign_in', cascade='all, delete-orphan')

class SignRecord(db.Model):
    """签到记录模型。"""
    __tablename__ = 'sign_record'
    id = db.Column(db.Integer, primary_key=True)
    sign_id = db.Column(db.Integer, db.ForeignKey('sign_in.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    sign_time = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(16), default='正常')

    student = db.relationship('User', backref='sign_records')
    __table_args__ = (db.UniqueConstraint('sign_id', 'student_id', name='_sign_student_uc'),)

class Notification(db.Model):
    """通知模型。"""
    __tablename__ = 'notification'
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text)
    create_time = db.Column(db.DateTime, default=datetime.now)

@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None