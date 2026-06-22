# -*- coding: utf-8 -*-
# PC (Windows) 端：
#  1. 訂閱 alarm/set, alarm/delete, alarm/clear -> 維護本地鬧鐘快取
#  2. 訂閱 sensor/data -> 取得 Pi 的溫濕度
#  3. 每秒檢查是否有鬧鐘時間到了 -> 選歌播放(跟Line Bot的響鈴推播同時觸發，不提前)
#  4. 播放時用 soundcard 抓 Windows 系統音訊 loopback，做真實 FFT 節奏分析
#     -> 把節奏強度 publish 到 led/control，給 Pi 控制 LED
#
# 安裝： pip install paho-mqtt soundcard numpy pygame

import json
import time
import random
import threading
import numpy as np
import soundcard as sc
import pygame
from datetime import datetime
import paho.mqtt.client as mqtt

PI_HOST = "192.168.0.121"  # 改成你 Pi 的實際 LAN IP

TOPIC_ALARM_SET = "alarm_system/alarm/set"
TOPIC_ALARM_DELETE = "alarm_system/alarm/delete"
TOPIC_ALARM_CLEAR = "alarm_system/alarm/clear"
TOPIC_SENSOR = "alarm_system/sensor/data"
TOPIC_LED = "alarm_system/led/control"
TOPIC_STOP_PLAYBACK = "alarm_system/playback/stop"    # Line Bot 收到 off/snooze 時發布
TOPIC_START_PLAYBACK = "alarm_system/playback/start"  # 開始提前播放時通知 Line Bot

pygame.mixer.init(frequency=44100, size=-16, channels=2)

music_playlist = {
    "lofi": ["audio/lofi_01.mp3", "audio/lofi_02.mp3"],
    "upbeat": ["audio/upbeat_01.mp3"],
    "calm": ["audio/calm_01.mp3"],
    "warm": ["audio/warm_01.mp3"],
    "energetic": ["audio/energetic_01.mp3"]
}

# =========================
# 共享狀態
# =========================
state_lock = threading.Lock()
alarms = []                # [{"time","uid","id"}]
sensor = {"temp": None, "hum": None}
triggered_alarm_ids = set()
stop_playback_flag = threading.Event()  # off/snooze 觸發時設定，播放迴圈會立刻停止


# =========================
# MQTT callbacks
# =========================
def on_connect(client, userdata, flags, rc):
    print("MQTT connected, rc =", rc)
    client.subscribe(TOPIC_ALARM_SET)
    client.subscribe(TOPIC_ALARM_DELETE)
    client.subscribe(TOPIC_ALARM_CLEAR)
    client.subscribe(TOPIC_SENSOR)
    client.subscribe(TOPIC_STOP_PLAYBACK)

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
    except Exception:
        return

    with state_lock:
        if msg.topic == TOPIC_ALARM_SET:
            alarms.append(data)

        elif msg.topic == TOPIC_ALARM_DELETE:
            alarms[:] = [a for a in alarms
                         if not (a["time"] == data.get("time") and a["uid"] == data.get("uid"))]

        elif msg.topic == TOPIC_ALARM_CLEAR:
            alarms.clear()
            triggered_alarm_ids.clear()

        elif msg.topic == TOPIC_SENSOR:
            sensor["temp"] = data.get("temp")
            sensor["hum"] = data.get("hum")

    # 在 lock 外處理，避免跟其他狀態操作搶鎖造成卡頓
    if msg.topic == TOPIC_STOP_PLAYBACK:
        stop_playback_flag.set()
        pygame.mixer.music.stop()

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
client.connect(PI_HOST, 1883, 60)
client.loop_start()


# =========================
# 選歌邏輯
# =========================
def get_music_category(temp, hum):
    score = {"lofi": 0, "upbeat": 0, "calm": 0, "warm": 0, "energetic": 0}

    # 🌡️ Temperature influence (Taiwan indoor, 4-tier)
    if temp < 22:
        score["warm"] += 2
        score["calm"] += 1
    elif 22 <= temp < 26:
        score["calm"] += 2
        score["upbeat"] += 1
    elif 26 <= temp < 30:
        score["upbeat"] += 2
        score["energetic"] += 1
    else:  # >= 30
        score["energetic"] += 2
        score["lofi"] += 1

    # 💧 Humidity influence (Taiwan-adjusted, 3-tier)
    if hum >= 75:
        score["lofi"] += 2
        score["calm"] += 1
    elif 50 <= hum < 75:
        score["calm"] += 1
        score["upbeat"] += 1
    else:  # < 50，通常是除濕/乾爽環境
        score["upbeat"] += 1
        score["energetic"] += 2

    return max(score, key=score.get)

def select_song(category):
    if category not in music_playlist:
        category = "calm"
    return random.choice(music_playlist[category])


# =========================
# 真實 FFT 節奏分析 (Windows: soundcard loopback)
# =========================
SAMPLE_RATE = 44100
BLOCK_SIZE = 1024

def fft_led_loop(duration_sec=45):
    """
    用 soundcard 抓系統預設輸出裝置的 loopback（不需要額外驅動，
    soundcard 在 Windows 上直接用 WASAPI loopback），
    對每個音框做 FFT，取低頻段能量當作節奏強度（bass-driven），
    再正規化成 0~1 發布給 Pi 控制 LED。
    播放與燈效固定跑 duration_sec 秒後自動結束（即使歌曲本身比較長）。
    """
    default_speaker = sc.default_speaker()
    mic = sc.get_microphone(id=str(default_speaker.name), include_loopback=True)

    start = time.time()
    with mic.recorder(samplerate=SAMPLE_RATE) as recorder:
        while pygame.mixer.music.get_busy():
            if (time.time() - start) > duration_sec:
                break
            if stop_playback_flag.is_set():
                break

            data = recorder.record(numframes=BLOCK_SIZE)  # shape: (BLOCK_SIZE, channels)
            mono = data.mean(axis=1)

            # FFT
            fft_vals = np.abs(np.fft.rfft(mono))
            freqs = np.fft.rfftfreq(len(mono), d=1.0 / SAMPLE_RATE)

            # 取低頻段 (20~250Hz，鼓點/bass主要energy集中區) 當節奏強度
            bass_mask = (freqs >= 20) & (freqs <= 250)
            bass_energy = fft_vals[bass_mask].mean() if bass_mask.any() else 0

            # 正規化 (用整體頻譜最大值做相對比例，避免每首歌音量不同造成誤判)
            overall_max = fft_vals.max() + 1e-6
            level = float(np.clip(bass_energy / overall_max * 3, 0, 1))  # *3 補償低頻通常佔比較小

            client.publish(TOPIC_LED, json.dumps({"level": level}))
            time.sleep(0.05)  # 約 20Hz 更新率，LED 看起來夠流暢

    pygame.mixer.music.stop()  # 🔥 時間到也要把音樂停掉，不能只熄燈
    client.publish(TOPIC_LED, json.dumps({"off": True}))
    stop_playback_flag.clear()  # 重置旗標，確保下一次鬧鐘能正常播放


# =========================
# 鬧鐘準時觸發 (不提前播放，跟正式響鈴同一時刻)
# =========================
def check_upcoming_alarms():
    with state_lock:
        alarms_copy = alarms[:]
        temp, hum = sensor.get("temp"), sensor.get("hum")

    if temp is None or hum is None:
        return  # 還沒收到 Pi 的感測器資料

    now = datetime.now()

    for a in alarms_copy:
        if a["id"] in triggered_alarm_ids:
            continue

        alarm_time = datetime.strptime(a["time"], "%H:%M").replace(
            year=now.year, month=now.month, day=now.day
        )
        diff = (alarm_time - now).total_seconds()

        if -10 <= diff <= 0:  # 時間到了才觸發，只容許檢查間隔造成的些微誤差(最多晚10秒)
            triggered_alarm_ids.add(a["id"])

            category = get_music_category(temp, hum)
            song = select_song(category)

            print(f"[{a['time']}] 溫:{temp} 濕:{hum} -> {category} -> {song}")

            # 🔥 通知 Line Bot：這個鬧鐘已經開始播放，
            # 讓使用者打 off/snooze 也能生效
            stop_playback_flag.clear()
            client.publish(TOPIC_START_PLAYBACK, json.dumps(a))

            try:
                pygame.mixer.music.load(song)
                pygame.mixer.music.play()
                fft_led_loop(duration_sec=45)
            except Exception as e:
                print("Playback error:", e)


def run_loop():
    print("PC monitor running...")
    while True:
        check_upcoming_alarms()
        time.sleep(1)  # 秒級檢查，確保準時進入前1分鐘窗口


if __name__ == "__main__":
    run_loop()