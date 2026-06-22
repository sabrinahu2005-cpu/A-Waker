# A-Waker: An Smart Context-Aware Music Alarm System

透過 **Line Bot** 設定鬧鐘 → **鬧鐘時間到時**依 Pi 偵測的即時溫濕度自動選歌 → **PC** 播放音樂並用 FFT 分析節奏 → 即時控制 **Pi 上的 LED 燈條**。

三個裝置透過 **MQTT** 互相同步，broker 架在 Pi 上。

---

## 系統架構

```
                ┌──────────────────────────┐
                │   MQTT Broker (Pi)       │  mosquitto, port 1883
                └────────────┬─────────────┘
       ┌────────────────────┼────────────────────┐
       │                    │                    │
 alarm/set              sensor/data          led/control
 alarm/delete
 alarm/clear
 playback/start
 playback/stop
       │                    │                    │
 LineBot (PC) 發布/訂閱   Pi 發布 (DHT11)    PC 發布 (FFT結果)
       │                    │                    │
       └─────────┬──────────┘                    │
                 │                               │
         PC (pc_music.py)                Pi (pi_node.py)
         收鬧鐘+溫濕度 → 選歌播放         訂閱節奏強度 → 控制LED
```

### 各程式部署位置

| 檔案 | 跑在哪 | 角色 |
|---|---|---|
| `pi_node.py` | Raspberry Pi | 每 10 秒讀 DHT11 發布溫濕度、訂閱 LED 指令驅動燈條 |
| `linebot_app.py` | PC | Line Bot Webhook 伺服器，管理鬧鐘清單並透過 MQTT 同步 |
| `pc_music.py` | PC | 訂閱鬧鐘與溫濕度，鬧鐘觸發時選歌播放，FFT 分析後發布 LED 指令 |

> **Broker 放 Pi 的原因**：Pi 通常 24 小時開機待命，broker 跟著「一直開著的那台」走最穩。

---

## MQTT Topic 一覽

所有 topic 以 `alarm_system/` 為前綴，避免與其他裝置撞名。

| Topic | 發布者 | 訂閱者 | Payload 範例 |
|---|---|---|---|
| `alarm_system/alarm/set` | Line Bot | PC | `{"time":"07:30","uid":"Uxx","id":"a1b2c3d4"}` |
| `alarm_system/alarm/delete` | Line Bot | PC | `{"time":"07:30","uid":"Uxx"}` |
| `alarm_system/alarm/clear` | Line Bot | PC | `{"uid":"Uxx"}` |
| `alarm_system/sensor/data` | Pi | PC | `{"temp":28,"hum":72}` |
| `alarm_system/led/control` | PC | Pi | `{"level":0.75}` 或 `{"off":true}` |
| `alarm_system/playback/start` | PC | Line Bot | `{"time":"07:30","uid":"Uxx","id":"a1b2c3d4"}` |
| `alarm_system/playback/stop` | Line Bot | PC | `{"uid":"Uxx"}` |

---

## 安裝與部署

### 前置需求

| 裝置 | 需求 |
|---|---|
| Raspberry Pi | Raspberry Pi OS、Python 3.8+、mosquitto |
| PC (Windows) | Python 3.8+、ngrok |
| 硬體 | DHT11 感測器、WS2812B LED 燈條（30 顆）、Line Developers 帳號 |

---

### Step 1｜Pi：安裝並啟動 MQTT Broker

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients
```

建立區網設定檔（允許非 localhost 連線）：

```bash
sudo nano /etc/mosquitto/conf.d/lan.conf
```

填入以下內容後儲存：

```
listener 1883
allow_anonymous true
```

套用並啟動服務：

```bash
sudo systemctl enable mosquitto
sudo systemctl restart mosquitto
# 確認正常運行
sudo systemctl status mosquitto
```

---

### Step 2｜Pi：安裝 Python 套件

```bash
pip3 install paho-mqtt adafruit-circuitpython-dht adafruit-blinka neopixel
```

---

### Step 3｜Pi：設定接線並啟動節點

打開 `pi_node.py`，依實際接線確認以下三個參數：

```python
DHT_PIN    = board.D4   # DHT11 訊號腳位（預設 GPIO 4）
LED_PIN    = board.D18  # LED 燈條訊號腳位（預設 GPIO 18，支援 PWM）
LED_COUNT  = 30         # 燈珠數量
```

啟動：

```bash
python3 pi_node.py
```

成功輸出：
```
MQTT connected, rc = 0
Pi node running, publishing sensor data & listening for LED control...
```

---

### Step 4｜PC：安裝 Python 套件

```bash
pip install paho-mqtt soundcard numpy pygame line-bot-sdk flask python-dotenv
```

---

### Step 5｜PC：設定環境變數

在 `linebot_app.py` 同層建立 `.env` 檔：

```
LINE_CHANNEL_ACCESS_TOKEN=你的 Channel Access Token
LINE_CHANNEL_SECRET=你的 Channel Secret
```

> 從 [Line Developers Console](https://developers.line.biz/) → Messaging API 頁面取得。

---

### Step 6｜PC：設定 Pi 的區網 IP

在 `linebot_app.py` 與 `pc_music.py` 裡，把以下這行改成 Pi 的實際 IP：

```python
PI_HOST = "192.168.0.121"  # ← 改成你的 Pi IP
```

查詢 Pi IP 的方式：

```bash
# 在 Pi 上執行
hostname -I
```

---

### Step 7｜PC：準備音樂檔案

在專案根目錄建立 `audio/` 資料夾，放入對應風格的 mp3 檔：

```
audio/
├── lofi_01.mp3
├── lofi_02.mp3
├── upbeat_01.mp3
├── calm_01.mp3
├── warm_01.mp3
└── energetic_01.mp3
```

對應 `pc_music.py` 裡的 `music_playlist`，可自行新增曲目（用 list 存多首，會隨機選一首播放）。

---

### Step 8｜PC：啟動兩個程式

各開一個終端機視窗分別執行：

```bash
# 視窗 1：Line Bot Webhook 伺服器
python linebot_app.py
```

```bash
# 視窗 2：選歌與 LED 控制
python pc_music.py
```

---

### Step 9｜PC：開 ngrok 並設定 Webhook

`linebot_app.py` 跑在本機 port 5000，需要用 ngrok 對外公開：

```bash
ngrok http 5000
```

複製 ngrok 給的 HTTPS 網址（例如 `https://xxxx.ngrok.io`），到 Line Developers Console → Messaging API → Webhook URL 填入：

```
https://xxxx.ngrok.io/callback
```

並開啟「Use webhook」。

---

## Line Bot 使用說明

加入 Line Bot 好友後，直接在聊天室輸入指令：

| 指令 | 說明 | 範例 |
|---|---|---|
| `set HH:MM` | 新增鬧鐘，可一次設多個 | `set 07:30` / `set 07:30 08:00 09:15` |
| `list` | 列出所有已設定的鬧鐘 | `list` |
| `delete HH:MM` | 刪除指定時間的鬧鐘 | `delete 07:30` |
| `delete all` | 清空所有鬧鐘 | `delete all` |
| `off` | 關閉目前正在響的鬧鐘 | `off` |
| `snooze` | 延後 5 分鐘再響 | `snooze` |

### 使用範例

```
你：set 07:30 08:00
Bot：已設定鬧鐘：
     07:30
     08:00

你：list
Bot：你的鬧鐘：
     ⏰ 07:30
     ⏰ 08:00

（07:30 到了）
Bot：⏰ 07:30 鬧鐘響了！

你：snooze
Bot：延後 5 分鐘 😴 → 07:35

你：off
Bot：關閉鬧鐘 ❌ 07:35
```

---

## 運作流程

```
1. 使用者在 Line 輸入 set 07:30
         │
         ▼
2. linebot_app.py 存入鬧鐘清單
   並 publish → alarm_system/alarm/set
         │
         ▼
3. pc_music.py 訂閱到，存入本地快取
   每秒比對系統時間
         │
         ▼ (07:30 整)
4. 同時觸發：
   ├─ linebot_app.py 推播 Line 訊息「⏰ 07:30 鬧鐘響了！」
   └─ pc_music.py 依當前溫濕度選歌並開始播放
         │
         │  溫濕度選歌邏輯（台灣室內校準）：
         │  🌡️ 溫度 4 段：<22°C→warm / 22-26→calm / 26-30→upbeat / ≥30→energetic
         │  💧 濕度 3 段：≥75%→lofi / 50-75%→calm+upbeat / <50%→energetic
         │  兩者各自累分，取最高分的類別
         │
         ▼
5. 播放期間，pc_music.py 用 soundcard 抓 Windows 系統音訊 loopback
   每 0.05 秒做一次 FFT，取低頻段（20–250 Hz）能量算節奏強度
   publish → alarm_system/led/control {"level": 0.0~1.0}
         │
         ▼
6. pi_node.py 收到 level，驅動 LED 流水動畫：
   level 越高 → 流動越快、偏暖色（紅）
   level 越低 → 流動越慢、偏冷色（藍）
   亮度鎖定 20%（安全用電）
         │
         ▼
7. 45 秒後自動停止播放＋熄燈
   或使用者回 off / snooze 立即停止
```

---

## 測試與除錯

### 測試 MQTT Broker 是否正常（在 Pi 上）

```bash
# 訂閱所有 topic，有訊息進來就會印出來
mosquitto_sub -h localhost -t "alarm_system/#" -v
```

啟動 PC 端程式後，應該能看到 `alarm_system/sensor/data` 每 10 秒跳出一筆溫濕度資料。

### 測試 PC 能否連上 Pi 的 Broker

```bash
# 在 PC 上執行
mosquitto_pub -h <Pi的IP> -p 1883 -t "test" -m "hello"
```

沒有錯誤訊息即代表連線成功。

### 常見問題排查

| 現象 | 可能原因 | 解法 |
|---|---|---|
| PC 連不上 Pi MQTT | 防火牆擋住 1883 port | `sudo ufw allow 1883` 或確認 `lan.conf` 放對位置後重啟 mosquitto |
| LED 沒有任何反應 | `pi_node.py` 未啟動，或 `LED_PIN` 設錯 | 確認程式有跑且 MQTT connected；用三用電表確認接線 |
| 溫濕度一直是 `None` | DHT11 接線錯誤或 `DHT_PIN` 對不上 | DHT11 偶爾讀取失敗是正常的，`pi_node.py` 會自動重試；若長時間無資料才需要查接線 |
| soundcard 抓不到音訊 | Windows 版本或音效驅動問題 | 確認預設播放裝置有音訊輸出；必要時在 `fft_led_loop()` 裡用 `sc.all_speakers()` 印出所有裝置名稱再手動指定 |
| 鬧鐘時間到但沒選歌 | 溫濕度尚未收到（`sensor` 為 `None`）| 確認 `pi_node.py` 在跑且 MQTT 通；`pc_music.py` 收到第一筆溫濕度前不會觸發 |
| Line Webhook 驗證失敗 | ngrok URL 沒更新到 Line Console | 每次重啟 ngrok 都會換網址，記得更新 Webhook URL |
| snooze 的時間跟現有鬧鐘撞 | 使用者本來就設了 5 分鐘後的鬧鐘 | 系統會自動偵測並取消延後，回覆說明原因 |

---

## 選歌邏輯說明

選歌由 `pc_music.py` 裡的 `get_music_category(temp, hum)` 計算，針對台灣室內環境校準：

```
溫度 (4 段)                        濕度 (3 段)
────────────────────────────       ──────────────────────────
< 22°C  → warm +2, calm +1         ≥ 75%      → lofi +2, calm +1
22–26°C → calm +2, upbeat +1       50–75%     → calm +1, upbeat +1
26–30°C → upbeat +2, energetic +1  < 50%      → upbeat +1, energetic +2
≥ 30°C  → energetic +2, lofi +1   （除濕後乾爽環境）
```

兩者分數累加，取最高分的類別播放對應風格的音樂。

---

## 未來可改進的方向

- **PC 重連時鬧鐘同步**：PC 斷線重連後會錯過中間的 `set`/`delete`，可加 `alarm/request_sync` topic，連線時向 Line Bot 要一份完整清單。
- **MQTT 安全性**：目前 `allow_anonymous true` 僅適合封閉區網；若要對外，需加帳密驗證與 TLS。
- **更精準的節拍偵測**：目前用低頻段能量近似，`librosa` 的 onset detection 可做更準確的鼓點分析。
- **音樂資料庫擴充**：現在每個類別只有 1–2 首，可串接 Spotify API 或本地更大的曲庫，依評分隨機加權選歌。
