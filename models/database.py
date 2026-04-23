from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime

# 数据库连接配置 (本地生成 huivision.db 文件)
SQLALCHEMY_DATABASE_URL = "sqlite:///./huivision.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 任务 4.2: 定义数据库 Schema
class VisionLog(Base):
    __tablename__ = "vision_logs"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String, unique=True, index=True) # 请求唯一标识 [cite: 87]
    image_path = Column(String)                         # 图片存储路径或元数据 [cite: 87]
    ai_result = Column(Text)                            # AI 识别结果全文 [cite: 87]
    first_token_latency = Column(Float)                 # 首字延迟 (ms) [cite: 87]
    total_latency = Column(Float)                       # 总延迟 (ms) [cite: 87]
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

# 初始化数据库表
def init_db():
    Base.metadata.create_all(bind=engine)