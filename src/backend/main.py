import os
import re
import uuid
import logging
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import fitz  # PyMuPDF
import requests
from sqlalchemy import create_engine, Column, String, Integer, JSON, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from fastapi.middleware.cors import CORSMiddleware

from pydantic import Field

# --------------------------
# 初始化配置
# --------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("deepquiz")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 数据库配置
SQLALCHEMY_DATABASE_URL = "sqlite:///./deepquiz.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# DeepSeek-R1 配置 (需替换为实际API信息)
DEEPSEEK_API_URL = "APIURL"
API_KEY = "APIKEY"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# --------------------------
# 数据库模型
# --------------------------
class QuizSession(Base):
    __tablename__ = "sessions"
    id = Column(String(36), primary_key=True)
    file_path = Column(String(512))
    config = Column(JSON)  # 存储难度、题目数量等配置
    content_summary = Column(Text)  # 解析后的文本摘要

class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True)
    session_id = Column(String(36))
    question_data = Column(JSON)  # 存储题目、选项、答案等

Base.metadata.create_all(bind=engine)

# --------------------------
# Pydantic模型
# --------------------------
class QuizConfig(BaseModel):
    num_questions: int = Field(gt=0, le=20, example=5)
    difficulty: str = "medium"
    question_types: List[str] = ["multiple_choice"]

class GeneratedQuestion(BaseModel):
    question: str
    options: List[str]
    answer: str
    explanation: str

# --------------------------
# 核心功能模块
# --------------------------
class PDFProcessor:
    @staticmethod
    def extract_text(file_path: str, max_length=3000) -> str:
        """提取PDF文本并进行清理"""
        doc = fitz.open(file_path)
        text = []
        for page in doc:
            text.append(page.get_text().strip())
        
        full_text = "\n".join(text)
        # 简单清理：移除多余空行和特殊字符
        cleaned = "\n".join([line.strip() for line in full_text.splitlines() if line.strip()])
        return cleaned[:max_length]  # 截断避免超出模型token限制

class LLMClient:
    @staticmethod
    def generate_questions(text: str, config: QuizConfig) -> List[GeneratedQuestion]:
        """调用大模型生成题目"""
        prompt = f"""
请基于以下教材内容，严格按照要求生成题目：

【教材内容】
{text[:2000]}

【生成要求】
1. 生成{config.num_questions}道题目，难度：{config.difficulty}
2. 题型分布：{config.question_types}
3. 必须使用以下格式：
   <题目开始>
   ###题型
   [题型名称]
   ###题目
   [题目正文]
   ###选项（仅客观题需要）
   A. [选项内容]
   B. [选项内容]
   C. [选项内容]
   D. [选项内容]
   ###答案
   [正确答案]
   ###解析
   [解题思路和依据]
   <题目结束>

【示例】
<题目开始>
###题型
multiple_choice
###题目
Python中用于文件打开的模式中，'w'表示？
###选项
A. 只读模式
B. 写入模式（覆盖）
C. 追加模式  
D. 二进制模式
###答案
B
###解析
'w'模式会覆盖已有文件内容，若需追加应使用'a'模式
<题目结束>

【重要规则】
1. 禁止添加额外解释
2. 答案必须唯一
3. 选项数量保持4个（多选题需明确标注正确选项数量）
        """
        
        try:
            # 人为进行了修改 START
            response = requests.post(
                DEEPSEEK_API_URL,
                headers=HEADERS,
                json={"messages": [{"role":"user","content":prompt}], "model": "deepseek-v3", "stream": False}
            )
            # 人为进行了修改 END
            response.raise_for_status()
            return LLMClient._parse_response(response.json())
        except Exception as e:
            logger.error(f"API调用失败: {str(e)}")
            raise

    @staticmethod
    def _parse_response(data: dict) -> List[dict]:
        """解析模型返回结果（需根据实际响应格式调整）"""
        # 示例解析逻辑，实际需要根据模型返回格式调整
        questions = []
        raw_response = data.get("choices", [{}])[0]["message"]["content"]

        # 去除Markdown格式
        raw_response = re.sub(r'```.*?\n', '', raw_response, flags=re.DOTALL)
        raw_response = raw_response.replace('\r\n', '\n').strip()

        # 分割独立题目
        raw_questions = re.split(r'<题目开始>', raw_response)
        
        for raw in raw_questions:
            if not raw.strip():
                continue

            try:
                # 使用正则表达式提取关键部分
                question_type = re.search(r'###题型\n(.+)', raw).group(1).strip()
                question = re.search(r'###题目\n(.+?)(?=\n###)', raw, re.DOTALL).group(1).strip()

                # 选项处理（可选）
                options_match = re.search(r'###选项\n(.+?)(?=\n###)', raw, re.DOTALL)
                options = [opt.strip() for opt in options_match.group(1).split('\n')] if options_match else []

                # 答案与解析
                answer = re.search(r'###答案\n(.+?)(?=\n###)', raw, re.DOTALL).group(1).strip()
                explanation = re.search(r'###解析\n(.+)', raw, re.DOTALL)
                explanation = explanation.group(1).strip() if explanation else ""

                if question_type == "multiple_choice" and "," in answer:
                    answer = "".join(sorted(answer.split(",")))

                # 构建结构化数据
                questions.append({
                    "question_type": question_type,
                    "question": question,
                    "options": options,
                    "answer": answer,
                    "explanation": explanation
                })
            except (AttributeError, ValueError) as e:
                logging.warning(f"题目解析失败，跳过该题。原始内容：\n{raw}\n错误：{str(e)}")
                continue

        return questions

# --------------------------
# API端点
# --------------------------
@app.post("/upload/")
async def upload_file(
    file: UploadFile = File(...),
    num_questions: int = Form(5),
    difficulty: str = Form("medium"),
    question_types: str = Form("multiple_choice")
):
    # 文件校验
    if not file.filename.endswith(".pdf"):
        raise HTTPException(400, "仅支持PDF文件")
    
    # 保存文件
    file_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, f"{file_id}.pdf")
    with open(file_path, "wb") as f:
        f.write(await file.read())
    
    # 解析文本
    try:
        text_content = PDFProcessor.extract_text(file_path)
    except Exception as e:
        raise HTTPException(500, f"PDF解析失败: {str(e)}")
    
    # 创建会话记录
    session = SessionLocal()
    db_session = QuizSession(
        id=file_id,
        file_path=file_path,
        config={
            "num_questions": num_questions,
            "difficulty": difficulty,
            "question_types": question_types.split(",")
        },
        content_summary=text_content[:1000]  # 存储摘要
    )
    session.add(db_session)
    session.commit()
    
    return JSONResponse({"session_id": file_id, "content_preview": text_content[:200]})

@app.post("/generate/{session_id}")
def generate_quiz(session_id: str):
    # 获取会话信息
    session = SessionLocal()
    db_session = session.query(QuizSession).filter_by(id=session_id).first()
    if not db_session:
        raise HTTPException(404, "会话不存在")
    
    # 生成题目
    try:
        config = QuizConfig(**db_session.config)
        questions = LLMClient.generate_questions(db_session.content_summary, config)
        
        # 存储题目
        for q in questions:
            session.add(Question(
                session_id=session_id,
                question_data=q
            ))
        session.commit()
        
        return {"count": len(questions), "questions": [q for q in questions]}
    except Exception as e:
        session.rollback()
        raise HTTPException(500, f"题目生成失败: {str(e)}")

@app.get("/questions/{session_id}")
def get_questions(session_id: str):
    """获取已生成的题目"""
    session = SessionLocal()
    questions = session.query(Question).filter_by(session_id=session_id).all()
    return {
        "data": [q.question_data for q in questions]
    }

# --------------------------
# 运行与中间件
# --------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)