import os
import json
import time
import requests
from google import genai
from google.genai import types
from playwright.sync_api import sync_playwright

# ================= 1. 金鑰與設定 (自動支援本地與 GitHub Actions) =================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")

client = genai.Client(api_key=GEMINI_API_KEY)

KEYWORDS = ["穿搭 推薦", "求推薦 外套", "韓國 穿搭"]

# ================= 2. LINE 推播函數 =================
def send_line_message(text):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN.strip()}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text}]
    }
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            print("   ✅ LINE 推播通知已成功發送！")
        else:
            print(f"   ❌ LINE 推播失敗 ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"   ❌ LINE 發送例外：{e}")

# ================= 3. Threads 爬蟲 (抓取最新貼文與連結) =================
def fetch_threads_posts(keyword, max_posts=2):
    posts_data = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, # 在 GitHub Actions 雲端執行時建議設為 True（無頭模式）
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        try:
            context = browser.new_context(
                storage_state="storage_state.json",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900}
            )
        except Exception:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900}
            )

        page = context.new_page()
        # 強制指定 sort=recent 抓取最新貼文
        search_url = f"https://www.threads.net/search?q={keyword}&serp_type=default&sort=recent"
        print(f"\n🔍 正在海巡【最新】貼文，關鍵字：【{keyword}】")
        page.goto(search_url)
        page.wait_for_timeout(4000)
        
        page.evaluate("window.scrollBy(0, 400)")
        page.wait_for_timeout(2000)
        
        post_containers = page.query_selector_all('div[data-pressable-container="true"]')
        
        filter_words = [
            "Threads", "登入", "註冊", "取得應用程式", "隱私權條款", 
            "回覆", "翻譯", "查看人們討論的主題", "使用 Instagram 帳號繼續"
        ]
        
        for container in post_containers:
            text = container.inner_text().strip()
            if text and len(text) > 15 and not any(w in text for w in filter_words):
                link_elem = container.query_selector('a[href*="/post/"]')
                post_url = ""
                if link_elem:
                    href = link_elem.get_attribute('href')
                    if href:
                        if href.startswith('http'):
                            post_url = href
                        else:
                            post_url = f"https://www.threads.net{href}"
                
                if not post_url:
                    post_url = search_url

                if not any(p['text'] == text for p in posts_data):
                    posts_data.append({"text": text, "url": post_url})
                    if len(posts_data) >= max_posts:
                        break
                    
        browser.close()
    return posts_data

# ================= 4. Gemini 意圖分析 =================
SYSTEM_PROMPT = """
你是一家韓系質感女裝服飾店的「Threads 海巡社群專員」。
請分析貼文內容，判斷對方是否有穿搭需求或購衣意圖。
必須回傳包含以下 key 的 JSON 物件：
1. is_target (boolean): true 代表是潛在客戶，false 代表不是。
"""

def analyze_and_notify(post_info):
    post_text = post_info["text"]
    post_url = post_info["url"]
    
    prompt = f"{SYSTEM_PROMPT}\n\n分析以下貼文：\n「{post_text}」"
    
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            
            result = json.loads(response.text)
            is_target = result.get('is_target', False)
            
            print(f"📌 貼文內容：{post_text[:30]}...")
            print(f"🎯 是否為潛在客戶：{'【是】' if is_target else '【否】'}")
            
            if is_target:
                msg = f"🔥 抓到最新潛在客戶 Threads 貼文！\n\n📌 內容：{post_text}\n\n🔗 貼文連結：\n{post_url}"
                send_line_message(msg)
            break
            
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                print(f"   ⚠️ 觸發 API 頻率限制，等待 60 秒恢復額度 (第 {attempt + 1}/3 次)...")
                time.sleep(60)
            elif "503" in err_msg or "UNAVAILABLE" in err_msg:
                print(f"   ⚠️ Gemini 伺服器忙碌，等待 15 秒後重試 (第 {attempt + 1}/3 次)...")
                time.sleep(15)
            else:
                print(f"   ❌ Gemini 分析出錯：{e}")
                break

# ================= 5. 主程序執行 =================
if __name__ == "__main__":
    print("🚀 開始執行 Threads 賣貨海巡機器人（最新貼文與連結模式）...")
    
    for kw in KEYWORDS:
        posts = fetch_threads_posts(kw, max_posts=2)
        print(f"📥 成功抓取 {len(posts)} 篇最新貼文，開始交給 Gemini 分析...")
        
        for post_info in posts:
            analyze_and_notify(post_info)
            print("⏳ 貼文分析完成，冷卻 40 秒以保護 API 免費額度...")
            time.sleep(40)
            
    print("\n🎉 海巡任務完成！")
