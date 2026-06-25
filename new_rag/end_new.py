
"""
边坡监测一体化上位机 - 完整版
单板WiFi方案：C8T6通过ESP8266 AP回传距离+GPS，支持远程控制LED/蜂鸣器
AI Agent：Ollama Qwen + 向量RAG + 短期/长期记忆 + 趋势预警
"""
import itchat
from email.utils import formataddr
from email.mime.text import MIMEText
import smtplib
import cv2
import queue
import threading
import numpy as np
import socket
import folium
import webbrowser
import os
import time
import sys
import json
import re
from PIL import Image, ImageTk, ImageDraw, ImageFont
from ultralytics import YOLO
import logging; logging.getLogger('ultralytics').setLevel(logging.ERROR)  # 关闭YOLO逐帧输出
import tkinter as tk
from tkinter import ttk, messagebox
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import serial
import serial.tools.list_ports


import requests
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import message_to_dict, messages_from_dict, BaseMessage
from typing import Sequence
from langchain_community.embeddings import OllamaEmbeddings
import os
from langchain_community.document_loaders import TextLoader
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaLLM, ChatOllama
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent


# ============================= # 参数设置区域 #=====================================#

CHUNK_SIZE = 200  # 分段的最大字符数
CHUNK_OVERLAP = 20  # 分段之间允许重叠的字符数
T = 2  # 返回最相似的k个文档

# ==================================================================================#


# =================================== 告警通知 ===================================#


class FakeStream:
    def write(self, *a): pass
    def flush(self): pass
    def fileno(self): return 1


if sys.stdout is None: sys.stdout = FakeStream()
if sys.stderr is None: sys.stderr = FakeStream()
os.environ["PYTHONUNBUFFERED"] = "1"

MAIL_CONFIG = {
    "sender": "2201719361@qq.com",
    "password": "paxzedexmxyydhbe",
    "receiver": "2201719361@qq.com",
    "smtp_server": "smtp.qq.com",
     "smtp_port": 465}
last_alert_time = 0
ALERT_COOLDOWN = 10


def send_email(msg):
    try:
        server = smtplib.SMTP_SSL(
    MAIL_CONFIG["smtp_server"],
     MAIL_CONFIG["smtp_port"])
        server.login(MAIL_CONFIG["sender"], MAIL_CONFIG["password"])
        m = MIMEText(msg, "plain", "utf-8")
        m["From"] = formataddr(["边坡监测系统", MAIL_CONFIG["sender"]])
        m["To"] = formataddr(["管理员", MAIL_CONFIG["receiver"]])
        m["Subject"] = "⚠️ 边坡监测告警"
        server.sendmail(
    MAIL_CONFIG["sender"], [
        MAIL_CONFIG["receiver"]], m.as_string())
        server.quit()
    except Exception as e: print("邮件发送失败:", e)


def send_wechat(msg):
    try: itchat.send(msg, toUserName="filehelper")
    except Exception as e: print("微信发送失败:", e)


def alert(typ, content):
    global last_alert_time
    now = time.time()
    if now - last_alert_time < ALERT_COOLDOWN: return
    last_alert_time = now
    text = f"【{typ}】{content}\n时间：{time.strftime('%Y-%m-%d %H:%M:%S')}"
    threading.Thread(target=send_wechat, args=(text,), daemon=True).start()
    threading.Thread(target=send_email, args=(text,), daemon=True).start()
    # 异步存事件到长期记忆(防崩溃)
    if 'agent' in globals() and agent is not None:
        try: agent._store_event(typ, content, current_distance if "边坡异常" in typ else None)
        except: pass

# =================================== 本地大模型 ===================================#


def ask_qwen(prompt):
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code != 200: return "(Ollama 未启动，请运行 ollama serve)"
    except: return "(Ollama 未启动，请运行 ollama serve)"

    llm = OllamaLLM(
        model="qwen2.5:3b-instruct",
        system="你是边坡监测智能助手。回答简短自然，不解释技术术语。不知道就说不知道，不编造数字。",
        timeout=20)
    try:
        return llm.invoke(prompt)

    except Exception as e:
        return f"调用出错:{e}"

# =================================== 向量RAG ===================================#


class VectorRAG:
    def __init__(self, doc_path="rag_docs"):
        self.vectorstore = None
        self.llm = None
        self.stats = {"total": 0, "facts": 0, "principles": 0, "hits": 0}
        self.strategy = os.environ.get("RAG_STRATEGY", "adaptive")  # adaptive | default
        if not os.path.exists(doc_path): print("知识库文件夹不存在"); return
        try:

            documents = []
            for f in os.listdir(doc_path):
                if f.endswith(".txt"):

                    loader = TextLoader(
                        os.path.join(doc_path, f),
                        encoding="utf-8")

                    documents.extend(loader.load())
            if not documents: print("没有找到有效文档"); return

            splitter = RecursiveCharacterTextSplitter(
                chunk_size=CHUNK_SIZE,
                chunk_overlap=CHUNK_OVERLAP,
                separators=["\n\n", "。", "\n", "？", "，", ",", ".", " ", ""],
                length_function=len
                )
            docs = splitter.split_documents(documents)

            embeddings = OllamaEmbeddings(model="qwen3-embedding:4b")
            self.vectorstore = FAISS.from_documents(docs, embeddings)
            self.llm = OllamaLLM(model="qwen2.5:3b-instruct", timeout=20)

            print(f"向量知识库构建完成，共 {len(docs)} 个片段")
        except Exception as e:
            print(f"向量索引构建失败:{e},降级为关键词搜索")
            self.vectorstore = None

    def ask(self, query, k=2):
        if self.vectorstore is None: return "知识库未加载"

        # 自适应检索：根据问题类型调整参数
        if self.strategy == "adaptive":
            qtype, adj_k = self._classify_question(query, k)
            self.stats["total"] += 1
            if qtype == "fact": self.stats["facts"] += 1
            elif qtype == "principle": self.stats["principles"] += 1
        else:
            adj_k = k
        docs = self.vectorstore.similarity_search(query, k=adj_k)
        if not docs: return "未找到相关内容"
        self.stats["hits"] += 1

        context = "\n\n".join([doc.page_content for doc in docs])

        prompt = ChatPromptTemplate.from_messages([
            ("system", "你是边坡监测智能助手。请根据以下参考资料回答问题，回答要简洁准确。参考资料：{context}"),
            ("user", "用户提问：{input}")
        ])
        chain = prompt | self.llm | StrOutputParser()
        return chain.invoke({"context": context, "input": query})

    def _classify_question(self, query, default_k):
        """分类问题类型并返回(k, chunk_k)"""
        fact_kw = ["阈值", "多少", "型号", "尺寸", "距离", "时间", "频率", "电压", "电流", "功率", "温度", "湿度", "速度", "速率", "几米", "几秒", "多大", "多长", "多重"]
        principle_kw = ["为什么", "原理", "如何", "原因", "机制", "方法", "流程", "算法", "结构", "组成", "体系", "架构", "介绍", "概述", "说明", "简介", "什么是"]
        if any(k in query for k in fact_kw):
            return ("fact", 2)       # 事实类：精确，少量
        elif any(k in query for k in principle_kw):
            return ("principle", 4)  # 原理类：更多上下文
        return ("other", default_k)

    def get_retrieval_stats(self):
        """返回检索统计"""
        total = self.stats.get("total", 0)
        return {
            "strategy": self.strategy,
            "total_queries": total,
            "fact_queries": self.stats.get("facts", 0),
            "principle_queries": self.stats.get("principles", 0),
            "hits": self.stats.get("hits", 0),
            "hit_rate": f"{self.stats.get('hits',0)/total*100:.0f}%" if total > 0 else "N/A"
        }


# ══ FileChatMessageHistory：持久化对话历史 ══
class FileChatMessageHistory(BaseChatMessageHistory):
    def __init__(self, session_id, storage_path="./chat_histories"):
        self.session_id = session_id
        self.store_path = storage_path
        self.file_path = os.path.join(self.store_path, self.session_id)
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        all_messages = list(self.messages)
        all_messages.extend(messages)
        new_messages = [message_to_dict(message) for message in all_messages]
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(new_messages, f)

    @property
    def messages(self) -> list[BaseMessage]:
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return messages_from_dict(json.load(f))
        except FileNotFoundError:
            return []

    def clear(self) -> None:
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump([], f)

# ══ ReAct Agent 工具函数 ══


@tool
def tool_get_distance() -> str:
    """获取当前边坡超声波距离传感器读数，返回单位为米的距离值和安全状态"""
    global current_distance
    d = current_distance
    if d <= 0: return "传感器暂无数据，请稍后再试"
    alert = "⚠️ 距离过近！" if d < self.DIST_THRESHOLD_SAFE else "✅ 安全距离"
    return f"当前边坡距离：{d:.2f}米。{alert}"


@tool
def tool_get_gps() -> str:
    """获取当前北斗GPS定位坐标，返回经度和纬度"""
    global current_lat, current_lng
    if abs(current_lat) < 0.01: return "GPS尚未定位，请确认天线已连接"
    return f"当前位置：纬度{current_lat:.6f}，经度{current_lng:.6f}"


@tool
def tool_get_weather(city: str = "北京") -> str:
    """查询指定城市的实时天气，参数city为城市名如北京、上海"""
    try:
        import requests
        geo = requests.get(
    f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=zh",
     timeout=5)
        if geo.status_code != 200 or not geo.json().get("results"): return f"未找到城市{city}"
        loc = geo.json()[
                       "results"][0]; lat, lon = loc["latitude"], loc["longitude"]
        r = requests.get(
    f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code",
     timeout=5)
        if r.status_code == 200:
            d = r.json()["current"]
            wcode = {
    0: "晴",
    1: "晴",
    2: "多云",
    3: "阴",
    45: "雾",
    51: "小雨",
    61: "中雨",
    71: "小雪",
    80: "阵雨",
    95: "雷阵雨"}.get(
        d.get(
            "weather_code",
            0),
             "未知")
            return f"{city}天气：{wcode}，温度{d['temperature_2m']}°C，湿度{d['relative_humidity_2m']}%"
    except: pass
    return f"{city}天气查询失败"


@tool
def tool_search_kb(query: str) -> str:
    """搜索边坡监测知识库，查询案例、原理、故障、规则等。参数query为搜索关键词或问题"""
    import end_new
    if hasattr(
        end_new, 'agent') and end_new.agent and end_new.agent.rag and end_new.agent.rag.vectorstore:
        return end_new.agent.search_kb(query)
    # 降级关键词匹配
    import glob
    kb_dir = "rag_docs"
    if not os.path.exists(kb_dir): return "知识库目录不存在"
    best_text = ""; best_score = 0; query_words = set(
        re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{2,}', query))
    for fname in sorted(glob.glob(os.path.join(kb_dir, "*.txt"))):
        try:
            with open(fname, encoding="utf-8") as f: text = f.read()
            score = sum(1 for w in query_words if w in text)
            if score > best_score: best_score = score; best_text = text[:400]
        except: continue
    return best_text if best_text else "未找到相关内容"

# =================================== Agent ===================================#


class SimpleAgent:

    DIST_THRESHOLD_SAFE = 0.5  # 安全阈值
    DIST_THRESHOLD_WARN = 0.3  # 警戒阈值

    def __init__(self):
        self.rag = None
        self.history = []                  # 仅用于上下文继承，非 LLM 历史
        self.stats = {"tool_calls": 0, "rag_hits": 0, "total_queries": 0}
        self.retry_count = 0; self.retry_success = 0  # 重试指标
        self.execution_log = []  # 可解释性推理日志
        self._mem_file = "memory.json"
        self.facts = self._load_facts()
        self._init_events_db()
        self._init_conversation_chain()  # LCEL 对话链（短期记忆+持久化）
        self._init_fact_store()          # FAISS 长期记忆
        self._init_react_agent()         # ReAct Agent（自主工具调用）

    # ══ 用户画像注入 ══
    def _get_user_profile(self):
        """读取用户画像（安全过滤，防prompt注入）"""
        name = self.facts.get("name", "")
        occ = self.facts.get("occupation", "")
        # 安全过滤：去除引号和换行
        for ch in ['\"', "'", "\n", "\r", "{", "}", "<", ">"]:
            name = name.replace(ch, "")
            occ = occ.replace(ch, "")
        parts = []
        if name: parts.append(f"姓名：{name}")
        if occ: parts.append(f"职业：{occ}")
        if parts:
            return "<用户画像>：" + "，".join(parts) + "</用户画像>"
        return ""

    # ══ 自进化：新案例写回知识库 ══
    def _save_case_to_kb(self, case_text):
        """将当前案例写入知识库，实现越用越好用"""
        try:
            ts=time.strftime("%Y-%m-%d %H:%M:%S")
            entry=f"[案例] {ts} {case_text}\n"
            os.makedirs("rag_docs",exist_ok=True)
            with open("rag_docs/cases.txt","a",encoding="utf-8") as f:
                f.write(entry)
            print(f"[进化] 新案例已写入知识库")
        except Exception as e:
            print(f"[进化] 写入失败:{e}")

    # ══ 对话摘要：截断前保留关键信息 ══
    def _summarize_conversation(self, old_msgs):
        """对即将被截断的消息生成摘要，存入长期记忆"""
        try:
            text = "\n".join([f"{'用户' if m.type=='human' else 'AI'}:{m.content[:100]}" for m in old_msgs[-10:]])
            prompt = f"请用一句话（30字以内）总结以下对话的核心内容：\n{text}"
            summary = ask_qwen(prompt)
            if summary and len(summary) > 5:
                # 存入 memory.json
                ts = time.strftime("%m-%d %H:%M")
                self.facts[f"对话摘要_{ts}"] = summary[:100]
                self._save_facts()
                # 同步写入 FAISS
                self._store_fact_to_faiss(f"对话摘要_{ts}", summary[:100])
                print(f"[摘要] {summary[:80]}")
        except Exception as e:
            print(f"[摘要] 生成失败:{e}")

    # ══ 短期记忆：RunnableWithMessageHistory (FileChatMessageHistory 持久化) ══
    def _init_conversation_chain(self):
        def get_session_history(session_id):
            hist = FileChatMessageHistory(
    session_id, storage_path="./chat_histories")
            msgs = hist.messages
            print(
    f"[对话历史] session={session_id} 已加载 {
        len(msgs)} msgs from file")
            # 滑动窗口：保留最近5轮，旧消息生成摘要
            if len(msgs) > 10:
                old_msgs = msgs[:len(msgs)-10]  # 即将被截掉的部分
                threading.Thread(target=self._summarize_conversation, args=(old_msgs,), daemon=True).start()
                hist.clear()
                hist.add_messages(msgs[-10:])
                print(f"[对话历史] truncated {len(msgs)} -> 10 msgs (已生成摘要)")
            return hist

        profile = self._get_user_profile()
        system_prompt = "你是边坡监测智能助手。禁止编造数字/坐标/天气数据。不知道就直说不知道。回答简短自然。"
        if profile:
            system_prompt += f" {profile}，请基于此个性化回复。"
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder("chat_history"),
            ("user", "{input}")
        ])
        llm = OllamaLLM(model="qwen2.5:3b-instruct", timeout=20)
        chain = prompt | llm | StrOutputParser()
        self.conversation_chain = RunnableWithMessageHistory(
            chain, get_session_history,
            input_messages_key="input",
            history_messages_key="chat_history"
            )
        print("[记忆] 对话链就绪 (FileChatMessageHistory)")

    # ══ 长期记忆：LangChain FAISS 向量存储 ══
    def _init_fact_store(self):  # 这个函数的目的在于把在memory.json文件中已经存好的事实给向量化
        try:
            self.fact_embeddings = OllamaEmbeddings(model="qwen3-embedding:4b")
            self.fact_store = None
            existing_facts = []
            for k, v in self.facts.items():
                # 用 f"{k}: {v}" 只是把它临时转成一条文本，只是为了把它喂给 FAISS
                # 进行向量化，而不是要改变它原本的字典性质。
                existing_facts.append(f"{k}: {v}")
            if existing_facts:  # 执行完之后，existing_facts 就是一个 列表，里面每一个元素都是 字符串。
                # 喂给 FAISS ，其中注意的是：进行向量化顺序是：先写 memory.json，后建 FAISS 索引。
                self.fact_store = FAISS.from_texts(
                    existing_facts, self.fact_embeddings)
                print(f"[长期记忆] 已加载 {len(existing_facts)} 条事实 (FAISS)")
            else:
                print("[长期记忆] 当前为空，将在首次存入时初始化")
        except Exception as e:
            print(f"[长期记忆] 初始化出错: {e}，降级使用字典存储")
            self.fact_store = None

    # 这个函数的目的在于把在新增的事实给向量化，而且这个函数本身，一次只更新“一条”事实。
    def _store_fact_to_faiss(self, key, value):
        """将单条事实存入 FAISS (LangChain 封装)"""
        try:
            text = f"{key}: {value}"
            if self.fact_store is None:
                self.fact_store = FAISS.from_texts(
                    [text], self.fact_embeddings)
            else:
                self.fact_store.add_texts([text])
        except Exception as e:
            print(f"[事实存储] 存储出错:{e}")

    def _query_fact_from_faiss(self, query):
        name_kw=["我叫什么","我是谁","还记得我叫","我的名字","什么名字","我叫啥","你记得我是谁吗","我叫什么名字"]
        for kw in name_kw:
            if kw in query:
                name=self.facts.get("name","")
                return f"用户的名字: {name}" if name else None
        occ_kw=["我是做什么","我是什么","我的职业","我的身份","我的角色","我在现场的角色","你记得我是做什么的"]
        for kw in occ_kw:
            if kw in query:
                occ=self.facts.get("occupation","")
                return f"用户的职业: {occ}" if occ else None
        if getattr(self,'fact_store',None) is None: return None
        try:
            docs=self.fact_store.similarity_search(query,k=1)
            if docs:
                content=docs[0].page_content
                if content.startswith("对话摘要"): return None
                return content
        except: pass
        return None

    # ══ ReAct Agent：自主工具调用 ══
    def _init_react_agent(self):
        try:
            model = ChatOllama(model="qwen2.5:3b-instruct", temperature=0)
            tools = [
    tool_get_distance,
    tool_get_gps,
    tool_get_weather,
     tool_search_kb,
     self._handle_slip_judgment,
     self._handle_sensor_check,
     self._handle_event_query]
            system = """你是边坡监测智能助手。你可以使用以下工具来获取实时数据：
- tool_get_distance: 获取当前超声波距离传感器读数
- tool_get_gps: 获取北斗GPS定位坐标
- tool_get_weather: 查询城市天气
- tool_search_kb: 搜索边坡监测知识库

规则：禁止编造数字/坐标/天气，所有实时数据必须通过工具获取。"""
            self.react_agent = create_react_agent(model, tools, prompt=system)
            print("[ReAct] agent 就绪")
        except Exception as e:
            print(f"[ReAct] 初始化出错:{e}，降级为规则匹配")
            self.react_agent = None

    def _load_facts(self):
        try:
            if os.path.exists(self._mem_file):
                # 这个函数是用来读取记忆文件的，也就是从 memory.json 里面加载之前保存的数据。
                with open(self._mem_file, encoding="utf-8") as f:
                    return json.load(f)  # 顺序是：先写 memory.json，后建 FAISS 索引。
        except Exception as e: print(f"[记忆] 加载出错:{e}")
        return {}

    def _save_facts(self):
        try:
            with open(self._mem_file, "w", encoding="utf-8") as f:
                json.dump(self.facts, f, ensure_ascii=False, indent=2)
        except Exception as e: print(f"[记忆] 保存出错:{e}")

    def _init_events_db(self):
        self.db_lock = threading.Lock()
        try:
            import sqlite3
            with self.db_lock:
                conn = sqlite3.connect(
    "slope_memory.db", check_same_thread=False)
                conn.execute("""CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT, event_type TEXT, description TEXT,
                    distance REAL, resolved BOOLEAN DEFAULT 0)""")
                conn.commit(); conn.close()
            print("[事件库] ready")
        except Exception as e: print(f"[事件库] init error:{e}")

    def _store_event(self, event_type, description, distance=None):
        try:
            import sqlite3
            with self.db_lock:
                conn = sqlite3.connect(
    "slope_memory.db", check_same_thread=False)
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                conn.execute("INSERT INTO events (timestamp,event_type,description,distance) VALUES (?,?,?,?)",
                             (ts, event_type, description[:200], distance))
                conn.commit(); conn.close()
            root.after(0,lambda e=event_type: log_to_gui(f"[事件库] 已存储: {e}"))
        except Exception as e: print(f"[事件库] 存储出错:{e}")

    def _recall_events(self, query_text, limit=10):
        try:
            import sqlite3
            with self.db_lock:
                conn = sqlite3.connect(
    "slope_memory.db", check_same_thread=False)
                results = []
                # 日期查询
                m = re.search(r'(\d+)月(\d+)[日号]', query_text)
                if m:
                    mon, day = m.group(1), m.group(2)
                    pat = f"{mon.zfill(2)}-{day.zfill(2)}%"
                    rows = conn.execute(
                        "SELECT timestamp,event_type,description,distance FROM events WHERE timestamp LIKE ? OR description LIKE ? ORDER BY timestamp DESC",
                        (f"%{pat}%", f"%{mon}%月%{day}%")).fetchall()
                    for ts, typ, desc, dist in rows:
                        st = ts[-14:-3] if len(ts) > 14 else ts
                        ds = f"({dist:.2f}m)" if dist else ""; sd = desc[:50] + (
                            "..." if len(desc) > 50 else "")
                        results.append(f"{st} {ds} {sd}")
                # 关键词搜索
                if not results:
                    words = re.findall(
    r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{2,}', query_text)
                    kw_list = [
    w for w in words if w not in (
        '什么',
        '怎么',
        '哪个',
        '哪些',
        '是',
        '的',
        '了',
        '吗',
        '呢',
        '吧',
         '啊')]
                    rowset = set()
                    for kw in kw_list[:8]:
                        rows = conn.execute(
                            "SELECT timestamp,event_type,description,distance FROM events WHERE event_type LIKE ? OR description LIKE ? OR timestamp LIKE ? ORDER BY timestamp DESC LIMIT ?",
                            (f"%{kw}%", f"%{kw}%", f"%{kw}%", limit)).fetchall()
                        for r in rows: rowset.add(r)
                        if len(rowset) >= limit: break
                    for ts, typ, desc, dist in sorted(
                        rowset, key=lambda r: r[0], reverse=True):
                        st = ts[-14:-3] if len(ts) > 14 else ts
                        ds = f"({dist:.2f}m)" if dist else ""; sd = desc[:50] + (
                            "..." if len(desc) > 50 else "")
                        results.append(f"{st} {ds} {sd}")
                # 兜底
                if not results:
                    rows = conn.execute(
                        "SELECT timestamp,event_type,description,distance FROM events ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
                    for ts, typ, desc, dist in rows:
                        st = ts[-14:-3] if len(ts) > 14 else ts
                        ds = f"({dist:.2f}m)" if dist else ""; sd = desc[:50] + (
                            "..." if len(desc) > 50 else "")
                        results.append(f"{st} {ds} {sd}")
                conn.close()
                return results
        except Exception as e:
            print(f"[事件库] recall error:{e}"); return []

    # ── 趋势与自检 ──
    def _check_trend(self):
        global dist_log, dist_log_lock
        if len(dist_log) < 5: return ""
        with dist_log_lock: recent = dist_log[-30:][:]
        first, last = recent[0], recent[-1]
        delta = first[1] - last[1]; elapsed = last[0] - first[0]
        if elapsed <= 0: return ""
        rate = delta / elapsed
        if delta > 0.05 and last[1] > self.DIST_THRESHOLD_WARN:
            return (f"趋势预警：过去{elapsed:.0f}秒内距离从{first[1]:.2f}m降至{last[1]:.2f}m,"
                   f"降幅{delta:.2f}m(速率{rate:.4f}m/s),虽未触发0.3m阈值但下降趋势明显,建议加强监测。")
        elif delta > 0.02 and last[1] > self.DIST_THRESHOLD_WARN:
            return f"当前距离{last[1]:.2f}m,近{elapsed:.0f}秒下降{delta:.2f}m,趋势平稳,但需持续观察。"
        elif delta > 0.2 and last[1] <= self.DIST_THRESHOLD_WARN:
            return f"⚠️ 距离已降至{last[1]:.2f}m且下降速度较快({delta:.2f}m/{elapsed:.0f}s),请立即关注！"
        return ""

    def _check_sensor_health(self):
        global dist_log, dist_log_lock
        if not dist_log: return ""
        now = time.time()
        with dist_log_lock:
            last_vals = dist_log[-10:][:]
            last = dist_log[-1]
        gap = now - last[0]
        if gap > 300: return f"传感器可能掉线：最后一次更新于{gap:.0f}秒前({gap / 60:.1f}分钟),请检查连接。"
        if len(last_vals) >= 10:
            vals = [v for _, v in last_vals]
            if len(set(vals)) == 1:
                ft = last_vals[0][0]; lt = last_vals[-1][0]
                if lt - ft > 300: return f"传感器可能卡死：最近5分钟内10次读数均为{vals[0]:.2f}m未变化,请检查传感器。"
        return ""

    # ── 工具 ──
    def get_distance(self):
        global current_distance
        for attempt in range(2):
            d = current_distance
            if d > 0:
                alert = "⚠️ 距离过近,边坡有坍塌风险！" if d < self.DIST_THRESHOLD_SAFE else "✅ 安全距离"
                if attempt > 0:
                    self.retry_success += 1
                    print(f"[重试] get_distance 第{attempt}次重试成功")
                return f"当前边坡距离：{d:.2f} 米。{alert}"
            if attempt == 0:
                self.retry_count += 1
                time.sleep(0.5)
                print(f"[重试] get_distance 第1次尝试失败，等待0.5秒重试...")
        return "传感器数据不可用，请稍后重试"

    def get_gps(self):
        global current_lat, current_lng
        if abs(current_lat) < 0.01: return "GPS尚未定位,请确认天线已连接"
        return f"当前位置：纬度 {current_lat:.6f},经度 {current_lng:.6f}"

    def get_weather(self, city):
        for attempt in range(2):
            try:
                import requests
                geo = requests.get(f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=zh",
                               timeout=5)
                if geo.status_code != 200 or not geo.json().get("results"):
                    return f"未找到城市'{city}'，请检查城市名"
                loc = geo.json()["results"][0]
                lat, lon = loc["latitude"], loc["longitude"]
                display_name = loc.get("name", city)
                r = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code",
                              timeout=5)
                if r.status_code == 200:
                    d = r.json()["current"]
                    wcode = {
    0: "晴",
    1: "晴",
    2: "多云",
    3: "阴",
    45: "雾",
    51: "小雨",
    53: "小雨",
    61: "中雨",
    63: "中雨",
    71: "小雪",
    73: "小雪",
    80: "阵雨",
    81: "阵雨",
    95: "雷阵雨"}.get(
        d.get(
            "weather_code",
            0),
             "未知")
                    if attempt > 0:
                        self.retry_success += 1
                        print(f"[重试] get_weather 第{attempt}次重试成功")
                    return f"{display_name} 当前天气：{wcode}，温度 {d['temperature_2m']}°C，湿度 {d['relative_humidity_2m']}%"
            except Exception as e:
                if attempt == 0:
                    self.retry_count += 1
                    print(f"[重试] get_weather 第1次失败({e})，1秒后重试...")
                    time.sleep(1)
        return "当前数据不可用，请稍后重试"

    def search_kb(self, query):
        if self.rag and self.rag.vectorstore is not None:
            try:
                result = self.rag.ask(query)
                if result and "未找到" not in result: self.stats["rag_hits"] += 1
                return result
            except Exception as e: print(f"向量RAG检索失败:{e}")
        # 降级关键词
        import glob
        kb_dir = "rag_docs"
        if not os.path.exists(kb_dir): return "知识库目录不存在"
        best_text = ""; best_score = 0
        query_words = set(
    re.findall(
        r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{2,}',
         query))
        case_words = ["案例", "事故", "滑坡", "历史", "发生", "监测", "数据", "记录", "报告"]
        for fname in sorted(glob.glob(os.path.join(kb_dir, "*.txt"))):
            try:
                with open(fname, encoding="utf-8") as f: text = f.read()
                score = sum(1 for w in query_words if w in text)
                if any(
    w in fname for w in [
        "case",
        "slope_cases",
         "alert"]): score += 3
                if any(w in query for w in case_words): score += 2
                if score > best_score: best_score = score; best_text = text[:400]
            except: continue
        if best_text: self.stats["rag_hits"] += 1; return best_text
        return "未找到相关内容,请尝试其他问题"

    def _extract_facts(self, text):
        facts = {}
        m = re.search(r"我叫\s*([\u4e00-\u9fa5a-zA-Z]+)", text)
        nm_candidate = m.group(1) if m else ""
        if nm_candidate and not any(
    w in nm_candidate for w in (
        "谁",
        "什么",
        "啥",
        "叫",
        "名字",
         "什么名字")): facts["name"] = nm_candidate
        m = re.search(r"我是\s*(?:一个|名)?\s*([\u4e00-\u9fa5a-zA-Z]+)", text)
        occ_candidate = m.group(1).rstrip("的") if m else ""
        if occ_candidate and not any(
    w in occ_candidate for w in (
        "谁",
        "什么",
        "啥",
        "做什么",
         "什么人")): facts["occupation"] = occ_candidate
        m = re.search(r"我喜欢\s*(.+?)(?:[。，、！？\n]|$)", text)
        if m and m.group(1).strip() not in (
    "什么", "啥"): facts["hobby"] = m.group(1).strip()
        m = re.search(r"我的\s*(.{2,10}?)\s*是\s*(.{2,20}?)(?:[。，、！？]|$)", text)
        if m: facts[m.group(1).strip()] = m.group(2).strip()
        return facts

    def _save_to_conversation(self, user_msg, ai_msg):
        """将所有对话（含工具调用直返）也写入 FileChatMessageHistory"""
        try:
            from langchain_core.messages import HumanMessage, AIMessage
            hist = FileChatMessageHistory(
    "default", storage_path="./chat_histories")
            hist.add_messages(
                [HumanMessage(content=user_msg), AIMessage(content=ai_msg)])
        except: pass

    # ══ 复合问题拆分 ══
    def _split_complex_query(self, text):
        """检测并拆分多意图问题"""
        # 按标点拆分
        parts = re.split(r'[？?！!。；;]', text)
        parts = [p.strip() for p in parts if len(p.strip()) > 2]
        if len(parts) > 1: return parts
        # 无标点：按关键词分组检测
        kw_groups = [
            ["距离", "多远", "多少米", "位移"],
            ["GPS", "经纬度", "定位", "坐标"],
            ["天气", "气温", "温度", "下雨"],
            ["告警", "事件", "历史", "发生过"],
            ["传感器", "正常吗", "健康", "掉线"],
            ["滑坡", "危险", "安全", "塌"],
        ]
        found = [
    i for i,
    grp in enumerate(kw_groups) if any(
        k in text for k in grp)]
        if len(found) > 1:
            # 至少两个不同意图 → 直接并行调用工具
            return ["__COMPOUND__"]  # 标记为复合，特殊处理
        return [text]

    # ══ 共用：城市名提取 ══
    def _extract_city(self, text):
        """从文本中提取城市名"""
        CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "西安", "重庆", "天津", "苏州", "长沙", "郑州", "东莞", "青岛", "沈阳", "大连", "昆明", "济南"]
        for city in CITIES:
            if city in text:
                return city
        m = re.search(r'([\u4e00-\u9fa5]{2,4})(?:的)?(?:天气|气温|温度|下雨)', text)
        if m:
            city = m.group(1)
            if city in ("这边", "那边", "这里", "那里", "当地", "现场", "本地"):
                return "北京"
            return city
        return "北京"

    # ══ 统一处理方法（消除重复）══
    @tool
    def _handle_slip_judgment(self, user_input=""):
        """滑坡风险诊断工具。当用户询问边坡安全、滑坡风险、是否危险、会不会塌时，调用此工具获取诊断结果和参考案例。"""
        global current_distance
        d = current_distance
        if d <= 0: return "当前无距离数据,请检查传感器连接。"
        if d > self.DIST_THRESHOLD_SAFE:
            ans = f"距离{d:.2f}m，暂时安全"
        elif d > self.DIST_THRESHOLD_WARN:
            ans = f"距离{d:.2f}m，警戒区间({self.DIST_THRESHOLD_WARN}-{self.DIST_THRESHOLD_SAFE}m)"
        else:
            ans = f"距离{d:.2f}m，高风险(>{self.DIST_THRESHOLD_WARN}m)"
        case = self.search_kb("滑坡案例 位移")
        if case and "未找到" not in case and len(case) > 20:
            ans += f"\n参考案例：{case[:200]}"
        self.stats["tool_calls"] += 1
        return ans

    @tool
    def _handle_sensor_check(self):
        """传感器状态诊断工具。当用户询问传感器是否正常、健康状态、有没有掉线时，调用此工具返回状态报告。"""
        global current_distance
        if current_distance <= 0: return "当前无距离数据,请检查传感器连接。"
        h = self._check_sensor_health()
        t = self._check_trend()
        ans = "传感器状态报告：\n"
        ans += h or "工作正常。\n"
        ans += t if t else f"当前距离：{current_distance:.2f}m。"
        return ans

    @tool
    def _handle_event_query(self, q, limit=5):
        """历史事件查询工具。当用户询问告警记录、历史事件、发生过什么时，调用此工具返回事件列表。"""
        evts = self._recall_events(q, limit=limit)
        if evts:
            NL = chr(10)
            return NL.join(["相关事件："] + evts[:8])
        return "暂无历史记录。"

    def get_distance_at(self, time_str):
        """查询历史时刻的距离"""
        global dist_history_with_time
        if not dist_history_with_time: return "暂无历史数据"
        if "昨天" in time_str: target=time.localtime(time.time()-86400)
        elif "前天" in time_str: target=time.localtime(time.time()-172800)
        elif "今天" in time_str and "什么时候" in time_str:
            ts,dist=dist_history_with_time[-1]
            return f"最近读数时间：{time.strftime('%H:%M:%S',time.localtime(ts))}，距离：{dist:.2f}m"
        else: return "暂不支持该时间查询"
        target_day=target.tm_yday; found=None
        for ts,dist in reversed(dist_history_with_time):
            if time.localtime(ts).tm_yday==target_day: found=(ts,dist); break
        if found:
            ts,dist=found
            return f"{target.tm_mon}月{target.tm_mday}日距离为{dist:.2f}m"
        return f"未找到{time_str}的数据"

    def _run_single(self, q):
        """处理单条子问题（避免递归死循环）"""
        if any(k in q for k in ["天气", "气温", "温度", "下雨"]):
            city = self._extract_city(q)
            try: return self.get_weather(city)
            except: return f"{city}天气查询失败"
        if any(k in q for k in ["距离", "多远", "多少米", "位移"]):
            self.stats["tool_calls"] += 1
            ans = self.get_distance()
            return ans if "暂无" not in ans else "数据暂不可用"
        if any(k in q for k in ["GPS", "经纬度", "定位", "坐标"]):
            self.stats["tool_calls"] += 1
            ans = self.get_gps()
            return ans if "暂无" not in ans else "数据暂不可用"
        if any(k in q for k in ["告警", "事件", "历史", "发生"]):
            return self._handle_event_query(q, limit=3)
        if any(k in q for k in ["传感器", "正常"]):
            return self._check_sensor_health() or "传感器正常"
        if any(k in q for k in ["滑坡", "危险", "安全", "塌"]):
            global current_distance
            d = current_distance
            if d <= 0: return "无距离数据"
            if d>self.DIST_THRESHOLD_SAFE: return f"距离{d:.2f}m，安全"
            if d>self.DIST_THRESHOLD_WARN: return f"距离{d:.2f}m，警戒"
            return f"距离{d:.2f}m，⚠️高风险"
        return None

    def _log_step(self, step_type, content):
        """记录推理步骤（最多10条）"""
        if len(self.execution_log) >= 10: return
        self.execution_log.append({
            "step": len(self.execution_log) + 1,
            "type": step_type,
            "content": str(content)[:150]
        })

    def run(self, user_input):
        global current_distance
        self.execution_log = []
        self.history.append(("用户", user_input))
        self.stats["total_queries"] += 1

        # ── 复合问题拆分 ──
        parts = self._split_complex_query(user_input)
        if parts == ["__COMPOUND__"]:
            # 多意图检测：直接调工具，用原始user_input保证城市名等上下文不丢失
            results = []
            if any(k in user_input for k in ["距离","多远","位移"]):
                results.append(self.get_distance())
            if any(k in user_input for k in ["GPS","经纬度","坐标","定位"]):
                results.append(self.get_gps())
            if any(k in user_input for k in ["天气","气温","温度","下雨"]):
                results.append(self.get_weather(self._extract_city(user_input)))
            if any(k in user_input for k in ["告警","事件","历史","发生"]):
                results.append(self._handle_event_query(user_input,limit=3))
            if any(k in user_input for k in ["传感器","正常","健康"]):
                results.append(self._handle_sensor_check())
            if any(k in user_input for k in ["滑坡","危险","塌","安全"]):
                results.append(self._handle_slip_judgment(user_input))
            if results:
                ans = "\n\n".join(results)
                self.history.append(("AI", ans)); self._save_to_conversation(user_input, ans); return ans
        elif len(parts) > 1:
            # 标点拆分 → 合并全部子句，用多意图检测处理
            results = []
            combined = " ".join(parts)
            if any(k in combined for k in ["距离","多远","位移"]):
                results.append(self.get_distance())
            if any(k in combined for k in ["GPS","经纬度","坐标","定位"]):
                results.append(self.get_gps())
            if any(k in combined for k in ["天气","气温","温度","下雨"]):
                results.append(self.get_weather(self._extract_city(combined)))
            if any(k in combined for k in ["告警","事件","历史","发生"]):
                results.append(self._handle_event_query(combined,limit=3))
            if any(k in combined for k in ["传感器","正常","健康"]):
                results.append(self._handle_sensor_check())
            if any(k in combined for k in ["滑坡","危险","塌","安全"]):
                results.append(self._handle_slip_judgment(combined))
            if results:
                ans = "\n\n".join(results)
                self.history.append(("AI", ans)); self._save_to_conversation(user_input, ans); return ans

        # 1. 事实提取 + 保存（JSON + FAISS 双写）
        new_facts = self._extract_facts(user_input)
        if new_facts:
            self.facts.update(new_facts); self._save_facts()
            # 同步写入 FAISS
            for k, v in new_facts.items(): self._store_fact_to_faiss(k, v)
        if "name" in new_facts: ans = f"记住啦,你叫{new_facts['name']}"; self.history.append(("AI", ans)); self._save_to_conversation(user_input, ans); return ans
        if "occupation" in new_facts: ans = f"记住啦,你是{new_facts['occupation']}"; self.history.append(("AI", ans)); self._save_to_conversation(user_input, ans); return ans
        if "hobby" in new_facts: ans = f"记住啦,你喜欢{new_facts['hobby']}"; self.history.append(("AI", ans)); self._save_to_conversation(user_input, ans); return ans

        # 2. 用户笔记
        if "记住：" in user_input or "请记住" in user_input:
            note = user_input.replace("记住：", "").replace("请记住", "").strip()
            self._store_event("user_note", note)
            fm = re.search(
    r'(.{1,10}?)(?:的(.{1,10}?))?是(.{1,20}?)(?:[。，、！？]|$)', note)
            if fm:
                k = fm.group(1).strip() + \
                             (f"_{fm.group(2).strip()}" if fm.group(2) else "")
                self.facts[k] = fm.group(3).strip(); self._save_facts()
            ans = "已记录！"; self.history.append(("AI", ans)); self._save_to_conversation(user_input, ans); return ans

        # ── 天气拦截：今天/现在/当前天气 → 直接走 _extract_city ──
        for p in ["今天天气","现在天气","当前天气"]:
            if p in user_input:
                city=self._extract_city(user_input)
                ans=self.get_weather(city)
                self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans

        # ── 指代消解 ──
        if "它" in user_input:
            if any(k in user_input for k in ["变化","趋势","下降","上升","速度","多久"]):
                trend=self._check_trend()
                ans=trend if trend else f"当前距离{current_distance:.2f}m，趋势平稳。"
                self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans
            else:
                ans=self.get_distance()
                self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans
        if "那次" in user_input and "告警" in user_input:
            events=self._recall_events("告警",limit=1)
            if events:
                dm=re.search(r'\((\d+\.\d+)m\)',events[0])
                ans=f"最近一次告警距离为{dm.group(1)}米。" if dm else f"最近一次告警：{events[0][:80]}"
            else:
                ans="暂无告警记录。"
            self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans
        # ── 海拔查询 ──
        if "海拔" in user_input or "高度" in user_input:
            global current_lat
            ans="当前GPS暂无海拔数据。" if abs(current_lat)>0.01 else "GPS尚未定位。"
            self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans
        # ── 预警时间估算 ──
        if "多久" in user_input and "预警" in user_input:
            global dist_log
            if current_distance<=0: ans="当前无距离数据。"
            elif len(dist_log)<3: ans="数据不足，无法估算。"
            else:
                first,last=dist_log[0],dist_log[-1]
                rate=(first[1]-last[1])/(last[0]-first[0]) if last[0]>first[0] else 0
                if rate<=0.0001: ans=f"当前距离{current_distance:.2f}m，趋势平稳，近期不会触发预警。"
                else:
                    t=(current_distance-0.3)/rate
                    ans=f"约{t:.0f}秒后触发预警。" if t<60 else f"约{t/60:.1f}分钟后触发预警。"
            self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans

        # ── 时间查询拦截 ──
        if "上次读数" in user_input:
            global dist_history_with_time
            if dist_history_with_time:
                ts, dist = dist_history_with_time[-1]
                timestr = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                ans = f"上次读数时间：{timestr}，距离：{dist:.2f}m"
            else:
                ans = "暂无历史读数记录"
            self.history.append(("AI", ans)); self._save_to_conversation(user_input, ans); return ans
        if "今天是什么日期" in user_input:
            ans = time.strftime("今天是 %Y年%m月%d日")
            self.history.append(("AI", ans)); self._save_to_conversation(user_input, ans); return ans
        if "昨天" in user_input or "前天" in user_input:
            ans = self.get_distance_at(user_input)
            self.history.append(("AI", ans)); self._save_to_conversation(user_input, ans); return ans

        # ── 强制工具调用：硬数据绝不经过大模型，防编造 ──
        hard_data = {
            "get_distance": ["距离", "多远", "多少米", "多少厘米", "位移"],
            "get_gps": ["GPS", "经纬度", "定位", "位置", "在哪", "海拔", "坐标"],
            "get_weather": ["天气", "气温", "温度", "下雨"],
        }


        for tool_name, keywords in hard_data.items():
            if any(k in user_input for k in keywords):
                if tool_name == "get_distance":
                    self._log_step("thought", "用户询问距离，🔒强制拦截")
                    ans = self.get_distance(); self.stats["tool_calls"] += 1
                    self._log_step("observation", ans[:100])
                elif tool_name == "get_gps":
                    self._log_step("thought", "用户询问GPS，🔒强制拦截")
                    ans = self.get_gps(); self.stats["tool_calls"] += 1
                    self._log_step("observation", ans[:100])
                elif tool_name == "get_weather":
                    if any(w in user_input for w in [
                           "哪边", "哪个", "比较", "vs", "VS"]):
                        break
                    ans = self.get_weather(self._extract_city(user_input))
                    self.stats["tool_calls"] += 1
                # 工具返回空/错误 → 统一提示
                if not ans or "暂无" in ans or "失败" in ans or "未连接" in ans:
                    ans = "当前传感器未连接或数据异常，请稍后重试"
                self.history.append(("AI", ans)); self._save_to_conversation(user_input, ans); return ans

        self._log_step("thought", "查询历史事件")
        # 历史事件强制查询
        evt_kw=["告警","事件","历史","发生过","记录","什么时候","哪些","发生了什么"]
        if any(k in user_input for k in evt_kw):
            ans=self._handle_event_query(user_input)
            self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans

        # 3. 查询自身信息（优先 FAISS 语义检索，回退 dict）
        name_q=["我叫什么","我是谁","还记得我叫","我的名字","什么名字","我叫啥"]
        if any(q in user_input for q in name_q):
            fact=self._query_fact_from_faiss("用户的名字")
            if fact and ":" in fact: n=fact.split(":",1)[1].strip()
            else: n=self.facts.get("name","")
            ans=f"你叫{n}呀" if n else "你还没告诉我你的名字,可以说'我叫XXX'让我记住哦！"
            self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans
        occ_q=["我是做什么","我是什么","我的职业","我的身份"]
        if any(q in user_input for q in occ_q):
            fact=self._query_fact_from_faiss("用户的职业身份")
            if fact and ":" in fact: occ=fact.split(":",1)[1].strip()
            else: occ=self.facts.get("occupation","")
            ans=f"你是{occ}" if occ else "我不知道,你可以告诉我"
            self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans
        hobby_q=["我喜欢什么","我的爱好","我有什么爱好","我爱什么"]
        if any(q in user_input for q in hobby_q):
            fact=self._query_fact_from_faiss("用户的爱好")
            if fact and ":" in fact: h=fact.split(":",1)[1].strip()
            else: h=self.facts.get("hobby","")
            ans=f"你喜欢{h}" if h else "我不知道,你可以告诉我"
            self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans

        # 4. 事实查询
        fm2=re.search(r'(.{1,16}?)(?:是什么|加固过吗|有没有加固)',user_input)
        if fm2:
            key=fm2.group(1).strip()
            if key in self.facts: ans=f"{key}是{self.facts[key]}"; self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans
            evts=self._recall_events(key)
            if evts: ans="\n".join(evts[:3]); self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans

        self._log_step("thought", "传感器自检")
        # 5. 传感器自检
        if any(k in user_input for k in ["传感器","检查","正常吗","掉线","卡死","健康","工作"]):
            ans=self._handle_sensor_check()  # 内部处理无数据
            self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans

        # 6. 趋势分析
        if any(k in user_input for k in ["趋势","下降","上升","怎么样","最近怎么样","当前状态","概况","总结"])\
           and not any(k in user_input for k in ["月","号","日","年","历史","记录","告警","天气","温度","气温","下雨"]):
            trend=self._check_trend()
            ans=trend if trend else f"当前距离{current_distance:.2f}m,趋势平稳。"
            self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans

        # 7. 上下文继承
        context_hints=["随便","来一个","举个例子","具体","详细","展开","继续","接着说",
                       "比","对比","和","变化","涨了","跌了","原来","刚才","上午","下午","昨天","今天"]
        if any(w in user_input for w in context_hints):
            for role,content in reversed(self.history):
                if role=="用户": user_input=f"{content} {user_input}"; break

        # 8. 反幻觉
        trend_kw=["比","对比","和","变化","涨了","跌了","趋势","原来","刚才","上午","下午","昨天","今天"]
        if any(k in user_input for k in trend_kw):
            if current_distance<=0:
                ans="当前无距离数据(传感器未连接或未测量),无法进行对比分析。"
                self.history.append(("AI",ans)); self._save_to_conversation(user_input,ans); return ans

        # 9. 闲聊
        casual=["你好","谢谢","再见","你是谁","你叫什么","你能做什么"]
        is_casual=any(w in user_input for w in casual)

        # 10. 滑坡判断
        slip_kw=["滑坡","危险","会滑坡吗","会不会塌","安全吗","判断","塌方","坍塌","风险"]
        if any(k in user_input for k in slip_kw):
            ans=self._handle_slip_judgment(user_input)
            self.stats["tool_calls"]+=1
            self.history.append(("Tool",f"slip: dist={current_distance:.2f}")); self.history.append(("AI",ans[:300])); self._save_to_conversation(user_input,ans[:300]); return ans

        # 11. ReAct Agent：自主决策调工具或直接回答
        response=None
        if not is_casual and self.react_agent is not None:
            self._log_step("thought", "进入ReAct Agent自主决策")
            try:
                from langchain_core.messages import HumanMessage
                # 加载短期记忆作为上下文
                # 注入用户画像到 ReAct Agent
                from langchain_core.messages import SystemMessage
                profile = self._get_user_profile()
                msgs = []
                if profile:
                    msgs.append(SystemMessage(content=f"你是边坡监测助手。{profile}"))
                hist=FileChatMessageHistory("default",storage_path="./chat_histories")
                recent_msgs=hist.messages[-10:] if hist.messages else []
                result=self.react_agent.invoke({"messages":msgs+recent_msgs+[HumanMessage(content=user_input)]})
                # 取最后一条 AI/Tool 消息
                msgs_out=result.get("messages",[])
                ai_msgs=[m for m in msgs_out if m.type=="ai" and hasattr(m,'content')]
                response=ai_msgs[-1].content if ai_msgs else None
                if response:
                    self.stats["tool_calls"]+=1
            except Exception as e:
                print(f"[ReAct] error:{e}")
                response=None

        # 12. 回退：直接回答
        if response is None:
            self._log_step("thought", "回退到LLM直接回答")
            config={"configurable":{"session_id":"default"}}
            response=self.conversation_chain.invoke({"input":user_input},config)
        self.history.append(("AI",response[:300]))
        return response

# =================================== 初始化 ===================================#
rag=None
agent=SimpleAgent()
def load_rag():
    global rag
    os.environ['HF_ENDPOINT']='https://hf-mirror.com'
    for attempt in range(3):
        try:
            rag=VectorRAG(); agent.rag=rag
            print(f"[知识库] loaded (attempt {attempt+1})")
            try: ai_status_val.config(text="AI运行中(RAG已就绪)",foreground=COLORS["success"])
            except: pass
            return
        except Exception as e:
            print(f"[知识库] attempt {attempt+1}/3 failed:{e}")
            if attempt<2: time.sleep(2)
    print("[知识库] 3次重试均失败,Agent将使用关键词降级搜索")
threading.Thread(target=load_rag,daemon=True).start()

# =================================== 全局配置 ===================================#
WIDTH,HEIGHT=640,480
CONF_THRESH=0.45
target_cls=[0,15,16]
mon_x1,mon_y1,mon_x2,mon_y2=160,100,480,380
COLORS={"primary":"#165DFF","success":"#00B42A","warning":"#FF7D00","danger":"#F53F3F","bg":"#F5F7FA","card_bg":"#FFFFFF"}
frame_queue=queue.Queue(maxsize=1)
current_lat,current_lng,current_distance,current_beijing_time=0.0,0.0,0.0,"--:--:--"
ai_warn_flag,cap_status=False,True
dist_history,time_history,count=[],[],0
dist_log=[]; dist_log_lock=threading.Lock()
dist_history_with_time=[]
tcp_cmd_queue=queue.Queue()

# =================================== 主窗口 ===================================#
root=tk.Tk()
root.title("边坡监测一体化上位机｜单板WiFi方案")
root.geometry("1920x1080"); root.minsize(1600,900); root.configure(bg=COLORS["bg"])
style=ttk.Style(root); style.theme_use("clam")
style.configure("Card.TFrame",background=COLORS["card_bg"])
style.configure("Title.TLabel",font=("微软雅黑",16,"bold"),foreground=COLORS["primary"])
style.configure("Ctrl.TButton",font=("微软雅黑",11),padding=6)
main_container=ttk.Frame(root,style="Card.TFrame",padding=20)
main_container.pack(fill=tk.BOTH,expand=True,padx=10,pady=10)

# =================================== 左侧===================================#
left_panel=ttk.Frame(main_container,style="Card.TFrame",width=360)
left_panel.pack(side=tk.LEFT,fill=tk.Y,padx=10,pady=10); left_panel.pack_propagate(False)
ttk.Label(left_panel,text="📡 实时监测数据",style="Title.TLabel").pack(pady=(20,5))
def card(parent,icon,title,default):
    f=ttk.Frame(parent,style="Card.TFrame",relief=tk.RIDGE,borderwidth=1)
    f.pack(fill=tk.X,padx=10,pady=6)
    ttk.Label(f,text=icon,font=("微软雅黑",16)).pack(side=tk.LEFT,padx=10,pady=12)
    ttk.Label(f,text=title,font=("微软雅黑",12)).pack(anchor="w",pady=(8,0))
    val=ttk.Label(f,text=default,font=("微软雅黑",14,"bold"),foreground=COLORS["primary"])
    val.pack(anchor="w",pady=(0,8)); return val
distance_val=card(left_panel,"📏","边坡距离","0.00 m")
lat_val=card(left_panel,"🌍","纬度","0.000000")
lng_val=card(left_panel,"🌍","经度","0.000000")
time_val=card(left_panel,"🕒","北斗时间","--:--:--")
wifi_status_label=card(left_panel,"🔌","WiFi连接状态","等待连接")
ai_status_val=card(left_panel,"👀","AI状态","未启动")
ttk.Label(left_panel,text="📈 边坡距离变化",style="Title.TLabel").pack(pady=(20,10))
fig=Figure(figsize=(3.3,3),dpi=100,facecolor=COLORS["card_bg"])
ax=fig.add_subplot(111); ax.set_facecolor("#F9FAFB")
ax.set_title("距离实时曲线",fontsize=10); ax.set_xlabel("时间点",fontsize=9); ax.set_ylabel("m",fontsize=9)
ax.grid(True,alpha=0.3); line,=ax.plot([],[],color=COLORS["primary"],linewidth=2,label="距离")
ax.legend(loc="upper right",fontsize=8)
canvas=FigureCanvasTkAgg(fig,left_panel); canvas.get_tk_widget().pack(fill=tk.BOTH,padx=10,pady=5)


# =================================== 中间视频 ===================================#
video_panel=ttk.Frame(main_container,style="Card.TFrame")
video_panel.pack(side=tk.LEFT,fill=tk.BOTH,expand=True,padx=10,pady=10)
ttk.Label(video_panel,text="👁️ AI智能入侵监测",style="Title.TLabel").pack(pady=(20,10))
video_canvas=tk.Canvas(video_panel,width=WIDTH,height=HEIGHT,bg="#222",bd=0,highlightthickness=0)
video_canvas.pack(pady=10)

# ── 系统监测日志（视频窗口下方）──
ttk.Label(video_panel,text="📋 系统监测日志",style="Title.TLabel").pack(pady=(10,5))
log_text=tk.Text(video_panel,height=12,state=tk.DISABLED,wrap=tk.WORD)
log_text.pack(fill=tk.BOTH,padx=10,pady=5)
def log_to_gui(msg):
    """将消息追加到系统日志文本框"""
    log_text.config(state=tk.NORMAL)
    tm=time.strftime("%H:%M:%S")
    log_text.insert(tk.END,f"[{tm}] {msg}\n")
    log_text.see(tk.END)
    log_text.config(state=tk.DISABLED)

# =================================== 右侧 ===================================#
right_panel=ttk.Frame(main_container,style="Card.TFrame",width=380)
right_panel.pack(side=tk.RIGHT,fill=tk.Y,padx=10,pady=10); right_panel.pack_propagate(False)
ttk.Label(right_panel,text="🗺️ 北斗定位地图",style="Title.TLabel").pack(pady=(20,10))
def open_map():
    if os.path.exists("slope_map.html"): webbrowser.open(f"file:///{os.path.realpath('slope_map.html')}")
    else: messagebox.showinfo("提示","暂无定位数据")
ttk.Button(right_panel,text="打开地图",command=open_map).pack(pady=5)

# =================================== 远程控制 ===================================#
ttk.Label(right_panel,text="🕹️ 远程控制",style="Title.TLabel").pack(pady=(20,10))
ctrl_frame=ttk.Frame(right_panel,style="Card.TFrame",relief=tk.RIDGE,borderwidth=1)
ctrl_frame.pack(fill=tk.X,padx=10,pady=5)
led_frame=ttk.Frame(ctrl_frame,style="Card.TFrame"); led_frame.pack(fill=tk.X,padx=5,pady=5)
ttk.Label(led_frame,text="💡 LED 控制",font=("微软雅黑",12,"bold")).pack(anchor="w")
led_state={"led1":False,"led2":False,"led3":False}; led_btn={}
def make_led_cmd(n,on): return f"CMD_LED_{n}_{'1' if on else '0'}_ENDLED_END"
def toggle_led(n,btn):
    led_state[f"led{n}"]=not led_state[f"led{n}"]; s=led_state[f"led{n}"]
    tcp_cmd_queue.put(make_led_cmd(n,s))
    btn.config(text=f"LED{n} ●" if s else f"LED{n} ○",foreground=COLORS["success"] if s else COLORS["danger"])
for i in[1,2,3]:
    row=ttk.Frame(led_frame,style="Card.TFrame"); row.pack(fill=tk.X,padx=10,pady=2)
    ttk.Label(row,text=f"LED{i}",font=("微软雅黑",10),width=6).pack(side=tk.LEFT)
    btn=ttk.Button(row,text=f"LED{i} ○",style="Ctrl.TButton"); btn.pack(side=tk.LEFT,padx=5)
    led_btn[f"led{i}"]=btn
led_btn["led1"].config(command=lambda: toggle_led(1,led_btn["led1"]))
led_btn["led2"].config(command=lambda: toggle_led(2,led_btn["led2"]))
led_btn["led3"].config(command=lambda: toggle_led(3,led_btn["led3"]))
buzz_frame=ttk.Frame(ctrl_frame,style="Card.TFrame"); buzz_frame.pack(fill=tk.X,padx=5,pady=5)
ttk.Label(buzz_frame,text="🔊 蜂鸣器控制",font=("微软雅黑",12,"bold")).pack(anchor="w")
buzz_state={"on":False}; buzz_btn=None
def toggle_buzzer(btn):
    buzz_state["on"]=not buzz_state["on"]
    tcp_cmd_queue.put(f"CMD_BUZZER_{'1' if buzz_state['on'] else '0'}_END")
    btn.config(text="🔔 蜂鸣器 ON" if buzz_state["on"] else "🔇 蜂鸣器 OFF",foreground=COLORS["danger"] if buzz_state["on"] else COLORS["success"])
buzz_row=ttk.Frame(buzz_frame,style="Card.TFrame"); buzz_row.pack(fill=tk.X,padx=10,pady=2)
buzz_btn=ttk.Button(buzz_row,text="🔇 蜂鸣器 OFF",style="Ctrl.TButton",command=lambda: toggle_buzzer(buzz_btn))
buzz_btn.pack(side=tk.LEFT,padx=5)
def all_off():
    for i in range(1,4):
        led_state[f"led{i}"]=False; tcp_cmd_queue.put(make_led_cmd(i,False))
        led_btn[f"led{i}"].config(text=f"LED{i} ○",foreground=COLORS["danger"])
    buzz_state["on"]=False; tcp_cmd_queue.put("CMD_BUZZER_0_END")
    buzz_btn.config(text="🔇 蜂鸣器 OFF",foreground=COLORS["success"])
ttk.Button(ctrl_frame,text="一键关闭全部",command=all_off).pack(pady=8)

# =================================== AI助手 ===================================#
ttk.Label(right_panel,text="🤖 AI Agent 智能助手",style="Title.TLabel").pack(pady=(10,10))
chat_display=tk.Text(right_panel,height=8,state=tk.DISABLED,wrap=tk.WORD)
chat_display.pack(padx=10,pady=5,fill=tk.BOTH)
chat_display.tag_config("user",foreground=COLORS["primary"]); chat_display.tag_config("ai",foreground=COLORS["success"])
chat_input=tk.Text(right_panel,height=2); chat_input.pack(padx=10,pady=5)
last_qa={"q":"","ans":""}
feedback_clicked=False

last_qa={"q":"","ans":""}; feedback_clicked=False

def save_feedback(q,ans):
    try:
        os.makedirs("./feedback",exist_ok=True)
        import json
        with open("./feedback/bad_cases.jsonl","a",encoding="utf-8") as f:
            NL=chr(10); f.write(json.dumps({"query":q,"answer":ans[:300],"timestamp":time.time()},ensure_ascii=False)+NL)
    except: pass

def send_chat():
    q=chat_input.get("1.0",tk.END).strip()
    if not q: return
    chat_input.delete("1.0",tk.END)
    
    feedback_clicked=False
    chat_display.config(state=tk.NORMAL)
    chat_display.insert(tk.END,f"你：{q}\n","user")
    chat_display.insert(tk.END,"AI：思考中...\n\n","ai")
    chat_display.config(state=tk.DISABLED); chat_display.see(tk.END)
    def do_agent():
        ans=agent.run(q)
        last_qa["ans"]=ans
        root.after(0,lambda:_show_answer(ans))
    threading.Thread(target=do_agent,daemon=True).start()

def _show_answer(ans):
    global feedback_clicked
    chat_display.config(state=tk.NORMAL)
    last_start=chat_display.index("end-2l linestart")
    last_line=chat_display.get(last_start,"end-1c")
    if "思考中" in last_line: chat_display.delete(last_start,"end-1c")
    chat_display.insert(tk.END,f"AI：{ans}\n\n","ai")
    feedback_clicked=False
    btn=ttk.Button(chat_display,text="👍 这个回答有用",style="Ctrl.TButton")
    def on_fb(b=btn):
        global feedback_clicked
        if not feedback_clicked:
            feedback_clicked=True
            save_feedback(last_qa["q"],last_qa["ans"])
            b.config(text="✅ 已记录",state=tk.DISABLED)
    btn.config(command=on_fb)
    chat_display.window_create(tk.END,window=btn)
    chat_display.insert(tk.END,"\n")
    chat_display.insert(tk.END,"\n")
    chat_display.config(state=tk.DISABLED); chat_display.see(tk.END)
ttk.Button(right_panel,text="发送提问",command=send_chat).pack(pady=5)
def show_stats():
    s=getattr(agent,'stats',{})
    msg=f"总查询：{s.get('total_queries',0)}\n工具调用：{s.get('tool_calls',0)}\nRAG命中：{s.get('rag_hits',0)}"
    messagebox.showinfo("运行统计",msg)
def show_reasoning():
    log=agent.execution_log
    if not log:
        messagebox.showinfo("推理过程","当前无推理记录")
        return
    lines=[]
    for entry in log:
        lines.append(f"[{entry['type']}] {entry['step']}: {entry['content'][:80]}")
    NL=chr(10); messagebox.showinfo("推理过程",NL.join(lines))
ttk.Button(right_panel,text="🧠 查看推理",command=show_reasoning).pack(pady=5)

ttk.Button(right_panel,text="📊 查看统计",command=show_stats).pack(pady=5)


# === 工具函数 ===
def weather_enhance(img): return cv2.convertScaleAbs(img,alpha=1.4,beta=20)
def put_chinese(img,text,pos,size=22,color=(0,255,0)):
    try:
        img_pil=Image.fromarray(cv2.cvtColor(img,cv2.COLOR_BGR2RGB))
        draw=ImageDraw.Draw(img_pil)
        try: font=ImageFont.truetype("C:/Windows/Fonts/simhei.ttf",size)
        except: font=ImageFont.truetype("simhei.ttf",size)
        draw.text(pos,text,font=font,fill=color[::-1])
        return cv2.cvtColor(np.array(img_pil),cv2.COLOR_RGB2BGR)
    except:
        cv2.putText(img,text,pos,cv2.FONT_HERSHEY_SIMPLEX,0.8,color,2); return img
def create_map(lat,lng):
    try:
        if abs(lat)<0.001 or abs(lng)<0.001: return
        m=folium.Map(location=[lat,lng],zoom_start=18)
        folium.Marker([lat,lng],icon=folium.Icon(color="red")).add_to(m)
        m.save("slope_map.html")
    except: pass

def update_display_data(distance):
    global current_distance,count,dist_log,dist_log_lock
    try:
        if distance==-1.0: distance_val.config(text="无信号/超出范围",foreground=COLORS["warning"]); return
        current_distance=round(float(distance),2)
        with dist_log_lock:
            dist_log.append((time.time(),current_distance))
            if len(dist_log)>100: dist_log.pop(0)
        distance_val.config(text=f"{current_distance:.2f} m",foreground=COLORS["primary"])
        dist_history.append(current_distance); time_history.append(count); count+=1
        if count%10==0 and current_distance>0: root.after(0,lambda: log_to_gui(f"[监测] 距离: {current_distance:.2f}m"))
        if len(dist_history)>50: dist_history.pop(0); time_history.pop(0)
        line.set_data(time_history,dist_history); ax.relim(); ax.autoscale_view(True,True,True); canvas.draw()
        if current_distance<0.3: alert("边坡异常",f"距离过近：{current_distance:.2f}m")
    except Exception as e: print("数据更新失败:",e)

def update_gps_data(lat,lng,alt,sats,tim):
    global current_lat,current_lng,current_beijing_time
    try:
        if abs(lat)>0.001 and abs(lng)>0.001:
            current_lat,current_lng=lat,lng
            lat_val.config(text=f"{lat:.6f}"); lng_val.config(text=f"{lng:.6f}")
            create_map(lat,lng)
        if tim and tim!="--:--:--": current_beijing_time=tim; time_val.config(text=tim)
    except: pass

# === WiFi TCP ===
def wifi_thread():
    server_ip="127.0.0.1"  # 真实C8T6用 192.168.0.48，模拟器用 127.0.0.1
    server_port=8080; poll_tick=0
    while True:
        try:
            conn=socket.socket(socket.AF_INET,socket.SOCK_STREAM); conn.settimeout(3)
            conn.connect((server_ip,server_port))
            root.after(0,lambda: wifi_status_label.config(text="✅ WiFi已连接",foreground=COLORS["success"]))
            print("[WiFi] 已连接到感知层")
            while True:
                try:
                    cmd=tcp_cmd_queue.get_nowait(); conn.sendall((cmd+"\r\n").encode("ascii"))
                    print(f"发送控制: {cmd}")
                except queue.Empty: pass
                if poll_tick%2==0: conn.sendall(b"GET_DIST\r\n")
                else: conn.sendall(b"GET_GPS\r\n")
                poll_tick+=1
                try: resp=conn.recv(512).decode("gbk",errors="ignore")
                except socket.timeout: time.sleep(0.3); continue
                if not resp: break
                for line in resp.split("\n"):
                    line=line.strip()
                    if line.startswith("DIST:"):
                        try: dist=float(line.split(":")[1]); root.after(0,update_display_data,dist)
                        except: pass
                    elif line.startswith("GPS:"):
                        try:
                            body=line.split(":")[1]; parts=body.split(",")
                            lat=float(parts[0]) if len(parts)>0 else 0.0
                            lng=float(parts[1]) if len(parts)>1 else 0.0
                            alt=float(parts[2]) if len(parts)>2 else 0.0
                            sats=parts[3] if len(parts)>3 else "0/0"
                            tim=parts[4].strip() if len(parts)>4 else "--:--:--"
                            root.after(0,update_gps_data,lat,lng,alt,sats,tim)
                        except Exception as e: print(f"GPS解析错误:{e}")
                time.sleep(0.2)
        except Exception as e:
            print(f"WiFi异常:{e}")
            root.after(0,lambda: wifi_status_label.config(text="❌ WiFi断开 重连中...",foreground=COLORS["danger"]))
            time.sleep(5)

# === AI监测 ===
def ai_monitor_thread():
    global ai_warn_flag
    cap=cv2.VideoCapture(0); cap.set(cv2.CAP_PROP_FRAME_WIDTH,WIDTH); cap.set(cv2.CAP_PROP_FRAME_HEIGHT,HEIGHT)
    model=YOLO("yolov8n.pt")
    root.after(0,lambda: ai_status_val.config(text="运行中",foreground=COLORS["success"]))
    while True:
        if not cap_status: time.sleep(0.1); continue
        ret,frame=cap.read()
        if not ret: root.after(0,lambda: ai_status_val.config(text="❌ 摄像头异常",foreground=COLORS["danger"])); time.sleep(1); continue
        frame=weather_enhance(frame)
        cv2.rectangle(frame,(mon_x1,mon_y1),(mon_x2,mon_y2),(0,255,255),2)
        frame=put_chinese(frame,"监测区域",(mon_x1,mon_y1-30),22,(0,255,255))
        results=model(frame,conf=CONF_THRESH); ai_warn_flag=False
        for box in results[0].boxes:
            cls=int(box.cls[0])
            if cls in target_cls:
                x1,y1,x2,y2=map(int,box.xyxy[0])
                cv2.rectangle(frame,(x1,y1),(x2,y2),(0,0,255),2)
                if not(x2<mon_x1 or x1>mon_x2 or y2<mon_y1 or y1>mon_y2): ai_warn_flag=True
        if ai_warn_flag:
            frame=put_chinese(frame,"⚠️ 入侵告警！",(20,40),26,(0,0,255))
            root.after(0,lambda: ai_status_val.config(text="⚠️ 入侵告警",foreground=COLORS["danger"]))
            alert("入侵检测","监测区域内有人/动物！")
        else:
            frame=put_chinese(frame,"🟢 正常监测",(20,40),22,(0,255,0))
            root.after(0,lambda: ai_status_val.config(text="运行中",foreground=COLORS["success"]))
        try:
            if not frame_queue.full(): frame_queue.put(frame.copy(),block=False)
        except: pass
    cap.release()

def update_video():
    try:
        if not frame_queue.empty():
            f=frame_queue.get(); f=cv2.cvtColor(f,cv2.COLOR_BGR2RGB)
            img=ImageTk.PhotoImage(Image.fromarray(f)); video_canvas.img=img
            video_canvas.create_image(0,0,anchor=tk.NW,image=img)
    except: pass
    root.after(20,update_video)

# === 串口GPS ===
def serial_gps_thread():
    while True:
        try:
            ser=serial.Serial("COM5",115200,timeout=1)
            while True:
                line=ser.readline().decode("gbk",errors="ignore").strip()
                if not line: continue
                if "时间" in line: root.after(0,lambda l=line: time_val.config(text=l))
                if "纬度" in line and "经度" in line:
                    parts=line.split(",")
                    if len(parts)>=2:
                        p0,p1=parts[0],parts[1]
                        root.after(0,lambda: lat_val.config(text=p0))
                        root.after(0,lambda: lng_val.config(text=p1))
        except:
            time.sleep(1)

# === 自主监控线程 ===
def autonomous_monitor():
    last_alert_time=0; last_alert_content=""
    time.sleep(10)  # 等 Agent 和传感器初始化
    while True:
        try:
            if current_distance<=0:
                pass  # 传感器无数据，静默跳过
            else:
                trend=agent._check_trend()
                if trend and ("预警" in trend or "⚠️" in trend or "风险" in trend):
                    now=time.time()
                    if now-last_alert_time>300 or trend!=last_alert_content:
                        # 检索历史案例 + 写回知识库（自进化）
                        case=agent.search_kb("距离下降 边坡 案例")
                        if case and "未找到" not in case:
                            trend+=f"\n参考案例：{case[:150]}"
                        agent._store_event("autonomous_alert",trend,current_distance)
                        agent._save_case_to_kb(trend)  # 写回知识库
                        last_alert_time=now; last_alert_content=trend
                        root.after(0,lambda t=trend: log_to_gui(f"[监控] ⚠️ {t[:120]}"))
            time.sleep(30)
        except Exception as e:
            print(f"[监控] 异常: {e}")
            time.sleep(30)

# === 启动 ===
if __name__=="__main__":
    threading.Thread(target=lambda: itchat.auto_login(hotReload=True),daemon=True).start()
    threading.Thread(target=wifi_thread,daemon=True).start()
    threading.Thread(target=serial_gps_thread,daemon=True).start()
    threading.Thread(target=ai_monitor_thread,daemon=True).start()
    threading.Thread(target=autonomous_monitor,daemon=True).start()
    update_video(); root.mainloop()
