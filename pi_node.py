# -*- coding: utf-8 -*-
# Pi 端：
#  1. 定期讀 DHT11，發布到 sensor/data
#  2. 訂閱 led/control，依 PC 算好的節奏強度控制 LED
#
# 跑法： python3 pi_node.py
# 需要： pip3 install paho-mqtt adafruit-circuitpython-dht adafruit-blinka neopixel

import time
import json
import threading
import board
import neopixel
import adafruit_dht
import paho.mqtt.client as mqtt

MQTT_HOST = "localhost"  # broker 就在本機
MQTT_PORT = 1883

TOPIC_SENSOR = "alarm_system/sensor/data"
TOPIC_LED = "alarm_system/led/control"

# =========================
# DHT11
# =========================
DHT_PIN = board.D4  # 依實際接線調整
dht = adafruit_dht.DHT11(DHT_PIN)

# =========================
# LED (沿用安全亮度設定：鎖死 20%)
# =========================
LED_COUNT = 30
LED_PIN = board.D18
SAFE_BRIGHTNESS = 0.2

pixels = neopixel.NeoPixel(LED_PIN, LED_COUNT, brightness=SAFE_BRIGHTNESS, auto_write=False)

# 流水燈狀態：用獨立執行緒持續推進位置，level 只決定「流動速度」跟「亮度」，
# 不再用閥值切整段燈，避免一格一格跳動造成閃爍感
led_lock = threading.Lock()
current_level = 0.0      # 最新收到的節奏強度 (0~1)
led_active = threading.Event()  # 是否要跑流水動畫
wave_position = 0.0      # 流水頭目前位置 (浮點數，可以平滑移動)

TAIL_LENGTH = 8  # 流水尾巴長度（幾顆燈做漸暗效果）

def set_led_level(level):
    global current_level
    with led_lock:
        current_level = max(0.0, min(1.0, level))
    led_active.set()

def led_off():
    led_active.clear()
    pixels.fill((0, 0, 0))
    pixels.show()

def led_animation_loop():
    """
    獨立跑流水動畫：wave_position 持續往前推進，
    每顆燈依距離流水頭多遠決定亮度(漸暗尾巴)，呈現滑動感而不是整段閃爍。
    節奏強度(level)越高 -> 流動越快、整體越亮、偏暖色；越低 -> 流動慢、偏冷色。
    """
    global wave_position
    while True:
        led_active.wait()  # 沒有播放時直接休眠，不空轉

        with led_lock:
            level = current_level

        # 速度：節奏弱時每秒走 ~3 顆燈，節奏強時每秒走 ~15 顆燈
        speed = 3 + level * 12
        wave_position = (wave_position + speed * 0.03) % LED_COUNT

        r = int(255 * level)
        b = int(255 * (1 - level))

        for i in range(LED_COUNT):
            # 計算這顆燈跟流水頭的距離（環狀，處理繞回起點的情況）
            dist = (wave_position - i) % LED_COUNT
            if dist < TAIL_LENGTH:
                fade = 1.0 - (dist / TAIL_LENGTH)  # 越靠近頭越亮
                brightness_scale = 0.3 + level * 0.7  # 節奏強整體更亮
                pixels[i] = (int(r * fade * brightness_scale), 0, int(b * fade * brightness_scale))
            else:
                pixels[i] = (0, 0, 0)

        pixels.show()
        time.sleep(0.03)  # ~33Hz 更新率，流動順暢

# =========================
# MQTT callbacks
# =========================
def on_connect(client, userdata, flags, rc):
    print("MQTT connected, rc =", rc)
    client.subscribe(TOPIC_LED)

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        if data.get("off"):
            led_off()
        elif "level" in data:
            set_led_level(data["level"])
    except Exception as e:
        print("LED control error:", e)

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

# =========================
# DHT11 背景發布
# =========================
def sensor_loop():
    while True:
        # DHT11 讀取常常失敗（硬體通訊協定本身就不穩），失敗就短暫重試幾次，
        # 避免長時間沒有新數據傳出去
        for attempt in range(3):
            try:
                t = dht.temperature
                h = dht.humidity
                if t is not None and h is not None:
                    client.publish(TOPIC_SENSOR, json.dumps({"temp": t, "hum": h}))
                    break
            except Exception as e:
                print(f"DHT11 read error (attempt {attempt+1}/3):", e)
                time.sleep(2)  # DHT11 兩次讀取間至少要間隔 2 秒
        time.sleep(8)  # 加上重試的 2~6 秒，整體約每 10 秒一個循環

if __name__ == "__main__":
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    threading.Thread(target=sensor_loop, daemon=True).start()
    threading.Thread(target=led_animation_loop, daemon=True).start()
    print("Pi node running, publishing sensor data & listening for LED control...")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        led_off()
        print("\n[通知] 程式已由使用者結束，燈帶已安全關閉。")