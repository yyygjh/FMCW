import cv2
import queue
import threading
import numpy as np
import socket
import folium
import webbrowser
import os
from PIL import Image, ImageTk, ImageDraw, ImageFont
from ultralytics import YOLO
import time
import tkinter as tk
from tkinter import ttk, messagebox
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import serial
import serial.tools.list_ports

# ==================== 启动画面（Splash Screen）====================
def show_splash():
    splash_root = tk.Tk()
    splash_root.overrideredirect(True)  # 去掉标题栏
    splash_root.attributes("-topmost", True) # 窗口置顶

    # 加载图片并自适应窗口
    try:
        splash_img = Image.open("splash.jpg")
        splash_img = splash_img.resize((800, 450), Image.Resampling.LANCZOS)
    except:
        # 无图片时用纯色背景
        splash_img = Image.new("RGB", (800, 450), color="#165DFF")
        draw = ImageDraw.Draw(splash_img)
        font = ImageFont.truetype("simhei.ttf", 40)
        draw.text((400, 225), "边坡监测系统", font=font, fill="white", anchor="mm")
        
    splash_photo = ImageTk.PhotoImage(splash_img)

    splash_label = tk.Label(splash_root, image=splash_photo)
    splash_label.image = splash_photo
    splash_label.pack()

    # 居中显示
    screen_width = splash_root.winfo_screenwidth()
    screen_height = splash_root.winfo_screenheight()
    x = (screen_width - 800) // 2
    y = (screen_height - 450) // 2
    splash_root.geometry(f"800x450+{x}+{y}")

    # 2秒后关闭启动画面，打开主界面
    splash_root.after(2000, splash_root.destroy)
    splash_root.mainloop()

# 运行启动画面
show_splash()

# ==================== 告警通知：微信 + 邮件 ====================
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr
import sys

# 【修复】itchat打包报错问题
class FakeStream:
    def write(self, *args): pass
    def flush(self): pass
    def fileno(self): return 1

if sys.stdout is None: sys.stdout = FakeStream()
if sys.stderr is None: sys.stderr = FakeStream()
os.environ["PYTHONUNBUFFERED"] = "1"
import itchat

MAIL_CONFIG = {
    "sender": "2201719361@qq.com",
    "password": "paxzedexmxyydhbe",
    "receiver": "2201719361@qq.com",
    "smtp_server": "smtp.qq.com",
    "smtp_port": 465
}

last_alert_time = 0
ALERT_COOLDOWN = 10

def send_email(msg):
    try:
        server = smtplib.SMTP_SSL(MAIL_CONFIG["smtp_server"], MAIL_CONFIG["smtp_port"])
        server.login(MAIL_CONFIG["sender"], MAIL_CONFIG["password"])
        msg = MIMEText(msg, "plain", "utf-8")
        msg["From"] = formataddr(["边坡监测系统", MAIL_CONFIG["sender"]])
        msg["To"] = formataddr(["管理员", MAIL_CONFIG["receiver"]])
        msg["Subject"] = "⚠️ 边坡监测告警"
        server.sendmail(MAIL_CONFIG["sender"], [MAIL_CONFIG["receiver"]], msg.as_string())
        server.quit()
    except Exception as e:
        print("邮件发送失败:", e)

def send_wechat(msg):
    try:
        itchat.send(msg, toUserName="filehelper")
    except Exception as e:
        print("微信发送失败:", e)

def alert(typ, content):
    global last_alert_time
    now = time.time()
    if now - last_alert_time < ALERT_COOLDOWN:
        return
    last_alert_time = now
    text = f"【{typ}】{content}\n时间：{time.strftime('%Y-%m-%d %H:%M:%S')}"
    threading.Thread(target=send_wechat, args=(text,), daemon=True).start()
    threading.Thread(target=send_email, args=(text,), daemon=True).start()

# ==================== 纯本地轻量 RAG ====================
class SimpleRAG:
    def __init__(self, doc_path="rag_docs"):
        self.knowledge = []
        if os.path.exists(doc_path):
            for f in os.listdir(doc_path):
                if f.endswith(".txt"):
                    try:
                        with open(os.path.join(doc_path, f), encoding="utf-8") as fp:
                            self.knowledge.append(fp.read())
                    except:
                        pass

    def ask(self, question):
        q = question.lower()
        for doc in self.knowledge:
            if any(word in doc.lower() for word in q.split()):
                return doc[:450] + "..."
        return "我是边坡监测AI助手，你可以问我：项目介绍、功能、故障、ESP8266、北斗/GPS、告警规则等问题。"

rag = SimpleRAG()

# ===================== 全局配置 =====================
WIDTH = 640
HEIGHT = 480
CONF_THRESH = 0.45
target_cls = [0, 15, 16]
mon_x1, mon_y1, mon_x2, mon_y2 = 160, 100, 480, 380

COLORS = {
    "primary": "#165DFF",
    "success": "#00B42A",
    "warning": "#FF7D00",
    "danger": "#F53F3F",
    "bg": "#F5F7FA",
    "card_bg": "#FFFFFF",
}

frame_queue = queue.Queue(maxsize=1)
current_lat = 0.0
current_lng = 0.0
current_distance = 0.0
current_beijing_time = "--:--:--"
ai_warn_flag = False
cap_status = True

dist_history = []
time_history = []
count = 0

# ===================== 主窗口 =====================
root = tk.Tk()
root.title("边坡监测一体化上位机｜双板数据融合")
root.geometry("1920x1080")
root.minsize(1600, 900)
root.configure(bg=COLORS["bg"])

style = ttk.Style(root)
style.theme_use("clam")
style.configure("Card.TFrame", background=COLORS["card_bg"])
style.configure("Title.TLabel", font=("微软雅黑", 16, "bold"), foreground=COLORS["primary"])

main_container = ttk.Frame(root, style="Card.TFrame", padding=20)
main_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

# ===================== 左侧面板 =====================
left_panel = ttk.Frame(main_container, style="Card.TFrame", width=360)
left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
left_panel.pack_propagate(False)

ttk.Label(left_panel, text="📡 实时监测数据", style="Title.TLabel").pack(pady=(20, 5))

def card(parent, icon, title, default):
    f = ttk.Frame(parent, style="Card.TFrame", relief=tk.RIDGE, borderwidth=1)
    f.pack(fill=tk.X, padx=10, pady=6)
    ttk.Label(f, text=icon, font=("微软雅黑", 16)).pack(side=tk.LEFT, padx=10, pady=12)
    ttk.Label(f, text=title, font=("微软雅黑", 12)).pack(anchor="w", pady=(8, 0))
    val = ttk.Label(f, text=default, font=("微软雅黑", 14, "bold"), foreground=COLORS["primary"])
    val.pack(anchor="w", pady=(0, 8))
    return val

distance_val = card(left_panel, "📏", "边坡距离", "0.00 m")
lat_val = card(left_panel, "🌍", "纬度", "0.000000")
lng_val = card(left_panel, "🌍", "经度", "0.000000")
time_val = card(left_panel, "🕒", "北斗时间", "--:--:--")
wifi_status_label = card(left_panel, "🔌", "WiFi连接状态", "等待连接")
serial_status_label = card(left_panel, "📡", "北斗串口状态", "等待连接")
ai_status_val = card(left_panel, "👀", "AI状态", "未启动")

ttk.Label(left_panel, text="📈 边坡距离变化", style="Title.TLabel").pack(pady=(20, 10))
fig = Figure(figsize=(3.3, 3), dpi=100, facecolor=COLORS["card_bg"])
ax = fig.add_subplot(111)
ax.set_facecolor("#F9FAFB")
ax.set_title("距离实时曲线", fontsize=10, fontproperties="SimHei")
ax.set_xlabel("时间点", fontsize=9)
ax.set_ylabel("m", fontsize=9)
ax.grid(True, alpha=0.3)
line, = ax.plot([], [], color=COLORS["primary"], linewidth=2, label="距离")
ax.legend(loc="upper right", fontsize=8)
canvas = FigureCanvasTkAgg(fig, left_panel)
canvas.get_tk_widget().pack(fill=tk.BOTH, padx=10, pady=5)

# ===================== 中间视频 =====================
video_panel = ttk.Frame(main_container, style="Card.TFrame")
video_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
ttk.Label(video_panel, text="👁️ AI智能入侵监测", style="Title.TLabel").pack(pady=(20, 10))
video_canvas = tk.Canvas(video_panel, width=WIDTH, height=HEIGHT, bg="#222", bd=0, highlightthickness=0)
video_canvas.pack(pady=10)

# ===================== 右侧面板 =====================
right_panel = ttk.Frame(main_container, style="Card.TFrame", width=380)
right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=10, pady=10)
right_panel.pack_propagate(False)

# 地图
ttk.Label(right_panel, text="🗺️ 北斗定位地图", style="Title.TLabel").pack(pady=(20, 10))

def open_map():
    if os.path.exists("slope_map.html"):
        webbrowser.open(f"file:///{os.path.realpath('slope_map.html')}")
    else:
        messagebox.showinfo("提示", "暂无定位数据")

ttk.Button(right_panel, text="打开地图", command=open_map).pack(pady=5)

# AI 助手
ttk.Label(right_panel, text="🤖 AI Agent 智能助手", style="Title.TLabel").pack(pady=(20, 10))
chat_display = tk.Text(right_panel, height=12, state=tk.DISABLED, wrap=tk.WORD)
chat_display.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)
chat_display.tag_config("user", foreground=COLORS["primary"])
chat_display.tag_config("ai", foreground=COLORS["success"])

chat_input = tk.Text(right_panel, height=2)
chat_input.pack(padx=10, pady=5)

# ===================== AI 问答（纯本地RAG）=====================
def send_chat():
    q = chat_input.get("1.0", tk.END).strip()
    if not q:
        return
    chat_input.delete("1.0", tk.END)
    chat_display.config(state=tk.NORMAL)
    chat_display.insert(tk.END, f"你：{q}\n", "user")
    ans = rag.ask(q)
    chat_display.insert(tk.END, f"AI：{ans}\n\n", "ai")
    chat_display.config(state=tk.DISABLED)
    chat_display.see(tk.END)

ttk.Button(right_panel, text="发送提问", command=send_chat).pack(pady=5)

# ===================== 工具函数 =====================
def weather_enhance(img):
    return cv2.convertScaleAbs(img, alpha=1.4, beta=20)

def put_chinese(img, text, pos, size=22, color=(0, 255, 0)):
    try:
        img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        font = ImageFont.truetype("simhei.ttf", size)
        draw.text(pos, text, font=font, fill=color[::-1])
        return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    except:
        cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        return img

def create_map(lat, lng):
    try:
        if abs(lat) < 0.001 or abs(lng) < 0.001:
            return
        m = folium.Map(location=[lat, lng], zoom_start=18)
        folium.Marker([lat, lng], icon=folium.Icon(color="red")).add_to(m)
        m.save("slope_map.html")
    except:
        pass

def update_display_data(distance):
    global current_distance, count
    try:
        if distance == -1.0:
            distance_val.config(text="无信号/超出范围", foreground=COLORS["warning"])
            return
        
        current_distance = round(float(distance), 2)
        distance_val.config(text=f"{current_distance:.2f} m", foreground=COLORS["primary"])

        dist_history.append(current_distance)
        time_history.append(count)
        count += 1
        if len(dist_history) > 50:
            dist_history.pop(0)
            time_history.pop(0)
        line.set_data(time_history, dist_history)
        ax.relim()
        ax.autoscale_view(True, True, True)
        canvas.draw()

        # 距离告警（0.3m 阈值）
        if current_distance < 0.3:
            alert("边坡异常", f"距离过近：{current_distance:.2f}m")
    except Exception as e:
        print("数据更新失败:", e)

def update_gps_data(lat, lng, time_str):
    global current_lat, current_lng, current_beijing_time
    try:
        if abs(lat) > 0.001 and abs(lng) > 0.001:
            current_lat = round(float(lat), 6)
            current_lng = round(float(lng), 6)
            lat_val.config(text=f"{current_lat:.6f}")
            lng_val.config(text=f"{current_lng:.6f}")
            create_map(current_lat, current_lng)
        
        if time_str and "2014-00-01" not in time_str:
            current_beijing_time = time_str
            time_val.config(text=current_beijing_time)
        
        serial_status_label.config(text="✅ 已连接", foreground=COLORS["success"])
    except Exception as e:
        print("GPS数据更新失败:", e)

# ===================== TCP线程（C8T6 超声波数据） =====================
def tcp_recv_thread():
    # C8T6 热点的IP和端口
    server_ip = "192.168.0.48"
    server_port = 8080
    while True:
        try:
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.connect((server_ip, server_port))
            print("已连接到C8T6服务器")
            wifi_status_label.config(text="✅ 已连接", foreground=COLORS["success"])
            while True:
                # 发送GET_DIST指令获取距离数据
                conn.sendall(b"GET_DIST\n")
                data = conn.recv(1024).decode("gbk", errors="ignore").strip()
                if data.startswith("DIST:"):
                    # 解析 DIST:2.74 m 格式
                    dist_str = data.split(":")[1].split()[0]
                    update_display_data(dist_str)
                elif data == "DIST:-1.00 m":
                    update_display_data(-1.0)
                time.sleep(1) # 每秒请求一次
        except Exception as e:
            print("WiFi连接断开，尝试重连:", e)
            wifi_status_label.config(text="❌ 断开", foreground=COLORS["danger"])
            time.sleep(3)

# ===================== 北斗串口线程（指南者GPS数据） =====================
# ===================== 北斗串口线程（终极版） =====================
def serial_gps_thread():
    while True:
        try:
            # 你的端口 COM5
            ser = serial.Serial("COM5", 115200, timeout=1)
            serial_status_label.config(text="✅ 已连接", foreground=COLORS["success"])
            
            while True:
                line = ser.readline().decode("gbk", errors="ignore").strip()
                if not line:
                    continue

                print("GPS 数据：", line)

                if "时间" in line:
                    time_val.config(text=line)
                if "纬度" in line and "经度" in line:
                    parts = line.split(",")
                    if len(parts)>=2:
                        lat_val.config(text=parts[0])
                        lng_val.config(text=parts[1])
                        
        except:
            serial_status_label.config(text="❌ 未连接", foreground=COLORS["danger"])
            time.sleep(1)
# ===================== AI监测线程 =====================
def ai_monitor_thread():
    global ai_warn_flag
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    model = YOLO("yolov8n.pt")
    ai_status_val.config(text="运行中", foreground=COLORS["success"])
    while True:
        if not cap_status:
            time.sleep(0.1)
            continue
        ret, frame = cap.read()
        if not ret:
            ai_status_val.config(text="❌ 摄像头异常", foreground=COLORS["danger"])
            time.sleep(1)
            continue
        frame = weather_enhance(frame)
        cv2.rectangle(frame, (mon_x1, mon_y1), (mon_x2, mon_y2), (0, 255, 255), 2)
        frame = put_chinese(frame, "监测区域", (mon_x1, mon_y1 - 30), 22, (0, 255, 255))
        results = model(frame, conf=CONF_THRESH)
        ai_warn_flag = False
        for box in results[0].boxes:
            cls = int(box.cls[0])
            if cls in target_cls:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                if not (x2 < mon_x1 or x1 > mon_x2 or y2 < mon_y1 or y1 > mon_y2):
                    ai_warn_flag = True
        if ai_warn_flag:
            frame = put_chinese(frame, "⚠️ 入侵告警！", (20, 40), 26, (0, 0, 255))
            ai_status_val.config(text="⚠️ 入侵告警", foreground=COLORS["danger"])
            alert("入侵检测", "监测区域内有人/动物！")
            root.attributes('-topmost', True)
            time.sleep(0.1)
            root.attributes('-topmost', False)
        else:
            frame = put_chinese(frame, "🟢 正常监测", (20, 40), 22, (0, 255, 0))
            ai_status_val.config(text="运行中", foreground=COLORS["success"])
        try:
            if not frame_queue.full():
                frame_queue.put(frame.copy(), block=False)
        except:
            pass
    cap.release()

# ===================== 视频刷新 =====================
def update_video():
    try:
        if not frame_queue.empty():
            f = frame_queue.get()
            f = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
            img = ImageTk.PhotoImage(Image.fromarray(f))
            video_canvas.img = img
            video_canvas.create_image(0, 0, anchor=tk.NW, image=img)
    except:
        pass
    root.after(20, update_video)

# ===================== 启动 =====================
if __name__ == "__main__":
    threading.Thread(target=lambda: itchat.auto_login(hotReload=True), daemon=True).start()
    threading.Thread(target=tcp_recv_thread, daemon=True).start()
    threading.Thread(target=serial_gps_thread, daemon=True).start()
    threading.Thread(target=ai_monitor_thread, daemon=True).start()
    update_video()
    root.mainloop()