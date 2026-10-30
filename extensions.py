"""第三方扩展实例化，避免循环导入。"""
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.login_message = '请先登录后再访问该页面。'
login_manager.login_message_category = 'warning'