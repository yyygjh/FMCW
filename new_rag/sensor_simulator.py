# -*- coding: utf-8 -*-
"""边坡监测感知层模拟器 — 模拟 C8T6 核心板：超声波距离 + GPS 定位"""
import socket, threading, time, random

HOST = "0.0.0.0"
PORT = 8080
distance = 1.20
gps_locked = True

def get_location():
    try:
        import urllib.request, json
        r = urllib.request.urlopen("http://ip-api.com/json/", timeout=3)
        data = json.loads(r.read())
        lat = data.get("lat"); lon = data.get("lon"); city = data.get("city", "")
        if lat and lon: return lat, lon, city
    except: pass
    return 37.07, 114.50, "邢台(默认)"

base_lat, base_lng, city = get_location()
print(f"  定位: {city} ({base_lat}, {base_lng})")

def generate_gps():
    lat = base_lat + random.uniform(-0.0001, 0.0001)
    lng = base_lng + random.uniform(-0.0001, 0.0001)
    return lat, lng

def generate_distance():
    global distance
    drift = -0.003
    noise = random.uniform(-0.02, 0.02)
    distance += drift + noise
    if random.random() < 0.02:
        distance -= random.uniform(0.05, 0.15)
        print(f"  ⚡ 模拟突发事件：距离骤降！")
    distance = max(0.05, min(3.0, distance))
    return distance

def handle_client(conn, addr):
    print(f"[连接] {addr}")
    try:
        conn.settimeout(5)
        while True:
            try:
                data = conn.recv(1024).decode("ascii", errors="ignore")
            except socket.timeout:
                continue
            if not data: break
            for line in data.split("\n"):
                line = line.strip()
                if not line: continue
                if "GET_DIST" in line:
                    d = generate_distance()
                    resp = f"DIST:{d:.2f}\r\n"
                    conn.sendall(resp.encode("ascii"))
                    tag = "⚠️" if d < 0.5 else "✅"
                    print(f"  GET_DIST → {d:.2f}m {tag}")
                elif "GET_GPS" in line:
                    if gps_locked:
                        lat, lng = generate_gps()
                        alt = random.uniform(15, 55)
                        sats = random.randint(8, 20)
                        tm = time.strftime("%H:%M:%S", time.localtime())
                        resp = f"GPS:{lat:.6f},{lng:.6f},{alt:.1f},{sats}/{sats+2},{tm}\r\n"
                    else:
                        resp = "GPS:0.0,0.0,0,0/0,--:--:--\r\n"
                    conn.sendall(resp.encode("ascii"))
                    print(f"  GET_GPS → {resp.strip()}")
                elif "CMD_" in line:
                    conn.sendall(b"OK\r\n")
                    print(f"  控制命令: {line}")
    except Exception as e:
        print(f"[断开] {e}")
    finally:
        conn.close()

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(1)
    print("=" * 50)
    print(f"  边坡感知层模拟器")
    print(f"  监听: {HOST}:{PORT}")
    print(f"  每15秒距离缓慢下降，偶发突变")
    print("=" * 50)
    print()
    print("  启动 end_new.py 后 WiFi 线程会自动连接")
    while True:
        conn, addr = server.accept()
        t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
        t.start()

if __name__ == "__main__":
    main()
