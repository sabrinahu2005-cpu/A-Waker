from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import threading
import time
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import uuid
import paho.mqtt.client as mqtt

# =========================
# INIT
# =========================
app = Flask(__name__)
load_dotenv()

line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

PI_HOST = os.getenv("PI_HOST", "192.168.0.121")  # 改成你 Pi 的實際 LAN IP

TOPIC_ALARM_SET = "alarm_system/alarm/set"        # 新增鬧鐘通知 PC
TOPIC_ALARM_CLEAR = "alarm_system/alarm/clear"    # 清空通知
TOPIC_ALARM_DELETE = "alarm_system/alarm/delete"  # 刪除單個通知
TOPIC_STOP_PLAYBACK = "alarm_system/playback/stop"    # 通知 PC 立刻停止播放音樂+LED
TOPIC_START_PLAYBACK = "alarm_system/playback/start"  # PC 開始提前播放時通知 Line Bot

# =========================
# GLOBAL STATE (Line Bot 仍是鬧鐘的主清單，PC 端訂閱同步)
# =========================
alarms = []  # [{"time", "uid", "id"}]
current_alarm = None
alarm_lock = threading.Lock()

# =========================
# MQTT CLIENT
# =========================
def on_mqtt_connect(client, userdata, flags, rc):
    print("MQTT connected, rc =", rc)
    client.subscribe(TOPIC_START_PLAYBACK)

def on_mqtt_message(client, userdata, msg):
    global current_alarm
    if msg.topic == TOPIC_START_PLAYBACK:
        try:
            data = json.loads(msg.payload.decode())
            with alarm_lock:
                current_alarm = (data["time"], data["uid"], data["id"])
            print(f"PC 開始提前播放：{data['time']}")
        except Exception as e:
            print("playback/start parse error:", e)

mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message
mqtt_client.connect(PI_HOST, 1883, 60)
mqtt_client.loop_start()  # 背景處理連線，不卡住 flask


# =========================
# WEBHOOK
# =========================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print("Webhook Error:", e)
        abort(400)
    return 'OK'


# =========================
# MESSAGE HANDLER
# =========================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    global alarms, current_alarm

    msg = event.message.text.strip()
    uid = event.source.user_id
    reply = ""

    # SET
    if msg.startswith("set"):
        try:
            times = msg.split()[1:]
            added, duplicates = [], []

            with alarm_lock:
                existing_times = {a["time"] for a in alarms if a["uid"] == uid}

                for t in times:
                    alarm_dt = datetime.strptime(t, "%H:%M")
                    t_norm = alarm_dt.strftime("%H:%M")  # 統一成兩位數格式，例如 "3:54" -> "03:54"

                    if t_norm in existing_times:
                        duplicates.append(t_norm)
                    else:
                        aid = str(uuid.uuid4())[:8]
                        record = {"time": t_norm, "uid": uid, "id": aid}
                        alarms.append(record)
                        existing_times.add(t_norm)
                        added.append(t_norm)
                        # 🔥 發布給 PC，讓 PC 提前 1 分鐘選歌
                        mqtt_client.publish(TOPIC_ALARM_SET, json.dumps(record))

            if added:
                reply = "已設定鬧鐘：\n" + "\n".join(added)
            if duplicates:
                reply += ("\n" if added else "") + "此鬧鐘已存在，略過：\n" + "\n".join(duplicates)

        except Exception:
            reply = "格式：set 07:30 08:00"

    # LIST
    elif msg == "list":
        with alarm_lock:
            user_alarms = sorted(a["time"] for a in alarms if a["uid"] == uid)
        reply = "你的鬧鐘：\n" + "\n".join(f"⏰ {t}" for t in user_alarms) if user_alarms else "目前沒有鬧鐘"

    # DELETE ALL
    elif msg == "delete all":
        with alarm_lock:
            alarms.clear()
            current_alarm = None
        mqtt_client.publish(TOPIC_ALARM_CLEAR, json.dumps({"uid": uid}))
        reply = "已刪除所有鬧鐘 🗑️"

    # DELETE ONE
    elif msg.startswith("delete"):
        try:
            t = datetime.strptime(msg.split()[1], "%H:%M").strftime("%H:%M")
            with alarm_lock:
                before = len(alarms)
                alarms[:] = [a for a in alarms if not (a["time"] == t and a["uid"] == uid)]
                removed = before - len(alarms)
            if removed:
                mqtt_client.publish(TOPIC_ALARM_DELETE, json.dumps({"time": t, "uid": uid}))
            reply = "已刪除" if removed else "找不到該鬧鐘"
        except Exception:
            reply = "格式：delete 20:00"

    # OFF
    elif msg == "off":
        with alarm_lock:
            if current_alarm:
                t, u, aid = current_alarm
                alarms[:] = [a for a in alarms if a["id"] != aid]
                current_alarm = None
                mqtt_client.publish(TOPIC_ALARM_DELETE, json.dumps({"time": t, "uid": u}))
                mqtt_client.publish(TOPIC_STOP_PLAYBACK, json.dumps({"uid": u}))
                reply = f"關閉鬧鐘 ❌ {t}"
            else:
                reply = "目前沒有正在響的鬧鐘"

    # SNOOZE
    elif msg == "snooze":
        with alarm_lock:
            if current_alarm:
                t, u, aid = current_alarm
                alarms[:] = [a for a in alarms if a["id"] != aid]

                new_time = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")

                # 防止跟現有鬧鐘時間衝突（例如使用者本來就設了同一個時間）
                existing_times = {a["time"] for a in alarms if a["uid"] == u}
                if new_time in existing_times:
                    mqtt_client.publish(TOPIC_STOP_PLAYBACK, json.dumps({"uid": u}))
                    current_alarm = None
                    reply = f"延後 5 分鐘的時間 {new_time} 跟現有鬧鐘重複，已取消延後"
                else:
                    new_id = str(uuid.uuid4())[:8]
                    record = {"time": new_time, "uid": u, "id": new_id}
                    alarms.append(record)
                    mqtt_client.publish(TOPIC_ALARM_SET, json.dumps(record))
                    mqtt_client.publish(TOPIC_STOP_PLAYBACK, json.dumps({"uid": u}))
                    current_alarm = None
                    reply = f"延後 5 分鐘 😴 → {new_time}"
            else:
                reply = "目前沒有正在響的鬧鐘"

    else:
        reply = msg

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))


# =========================
# 鬧鐘觸發檢查 (本地排程，跟原版邏輯一致)
# =========================
def check_alarms():
    global current_alarm

    now = datetime.now().strftime("%H:%M")

    with alarm_lock:
        alarms_copy = alarms[:]

    for a in alarms_copy:
        if a["time"] == now:
            with alarm_lock:
                if a not in alarms:
                    continue
                alarms[:] = [x for x in alarms if x["id"] != a["id"]]
                current_alarm = (a["time"], a["uid"], a["id"])

            try:
                line_bot_api.push_message(a["uid"], TextSendMessage(text=f"⏰ {a['time']} 鬧鐘響了！"))
            except Exception as e:
                print("Push Error:", e)
            return


def run_scheduler():
    while True:
        check_alarms()
        time.sleep(5)


if __name__ == "__main__":
    threading.Thread(target=run_scheduler, daemon=True).start()
    print("Bot running...")
    app.run(port=5000)