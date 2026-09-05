""" SQLAlchemy 2.0 声明式基类 ，所有ORM模型都继承自Base"""

from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
