import os
import json
import time
import requests
from google import genai
from google.genai import types
from playwright.sync_api import sync_playwright

# =============================== 1. 金鑰與設定 ===============================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")

client = genai.Client(api_key=GEMINI_API_KEY)

KEYWORDS = ["韓系", "套裝", "包包", "娃衣", "追星", "外套", "星星", "波點", "帽T", "吊飾", "錢包", "褲子", "彎刀褲", "七分褲", "闊腿褲", "薄巧"]

# =============================== 2. LINE 推播函數 ===============================
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
            print(" ✅ LINE 推播通知已成功發送！")
        else:
            print(f" ❌ LINE 推播失敗：{response.status_code} - {response.text}")
    except Exception as e:
        print(f" ❌ LINE 傳送異常：{e}")

# =============================== 3. 時間過濾輔助函數 ===============================
def is_within_7_days(post_text):
    # 檢查是否含有明顯的舊年份（例如 2025）
    if "2025" in post_text:
        return False
    
    # 簡單過濾週數過大或月份字眼（若文章寫到幾週前、幾個月前則排除）
    for w in ["週前", "個月前", "年前"]:
        if w in post_text:
            return False
            
    return True

# =============================== 4. Playwright 爬蟲函數 ===============================
def scrape_threads(keyword, max_posts=3):
    print(f" 🔍 正在海巡【7天內最新】貼文，關鍵字：[{keyword}]...")
    posts_data = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        if os.path.exists("storage_state.json"):
            try:
                context = browser.new_context(storage_state="storage_state.json")
            except Exception:
                pass
                
        page = context.new_page()
        search_url = f"https://www.threads.net/search?q={urllib_quote(keyword)}&serp_sub_type=recent"
        
        try:
            page.goto(search_url, timeout=60000)
            time.sleep(5)
            
            for _ in range(2):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(3)
                
            posts = page.locator('div[data-pressable-container="true"]').all()
            for post in posts:
                try:
                    text = post.inner_text()
                    if not text or len(text.strip()) < 10:
                        continue
                        
                    # 執行 7 天內時間過濾
                    if not is_within_7_days(text):
                        continue
                        
                    link_elem = post.locator('a[href*="/@"]').first
                    href = link_elem.get_attribute("href") if link_elem.count() > 0 else ""
                    
                    if href.startswith("http"):
                        post_url = href
                    else:
                        post_url = f"https://www.threads.net{href}"
                        
                    if not post_url or "search" in post_url:
                        continue
                        
                    if not any(p['text'] == text for p in posts_data):
                        posts_data.append({"text": text, "url": post_url})
                        if len(posts_data) >= max_posts:
                            break
                except Exception:
                    continue
                    
        except Exception as e:
            print(f" ⚠️ 爬蟲過程發生錯誤：{e}")
            
        browser.close()
        return posts_data

def urllib_quote(string):
    import urllib.parse
    return urllib.parse.quote(string)

# =============================== 5. Gemini 意圖分析 ===============================
SYSTEM_PROMPT = """
你是一家韓系質感女裝服飾店的「Threads 海巡社群專員」。
請分析貼文內容，判斷對方是否有穿搭需求或購衣意圖。
必須回傳包含以下 key 的 JSON 物件：
1. is_target (boolean): true 代表是潛在客戶，false 代表不是。
"""

def analyze_and_notify(post_info):
    post_text = post_info["text"]
    post_url = post_info["url"]
    
    prompt = f"{SYSTEM_PROMPT}\n\n請分析以下貼文：\n{post_text} "
    
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            
            result = json.loads(response.text)
            is_target = result.get('is_target', False)
            
            print(f" 📌 貼文內容 : {post_text[:30]}...")
            print(f" 🎯 是否為潛在客戶 : { '【是】' if is_target else '【否】' }")
            
            if is_target:
                msg = f"🔥 抓到最新潛在客戶 Threads 貼文！\n\n📌 內容 : {post_text}\n\n🔗 貼文連結 : \n{post_url}"
                send_line_message(msg)
            break
            
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                print(f" ⚠️ 觸發 API 頻率限制，等待 60 秒恢復額度（第 {attempt + 1}/3 次）...")
                time.sleep(60)
            elif "503" in err_msg or "UNAVAILABLE" in err_msg:
                print(f" ⚠️ Gemini 伺服器忙碌，等待 15 秒後重試（第 {attempt + 1}/3 次）...")
                time.sleep(15)
            else:
                print(f" ❌ Gemini 分析出錯：{e}")
                break
        
        time.sleep(40)

# =============================== 6. 主程式執行 ===============================
if __name__ == "__main__":
    print(" 🚀 開始執行 Threads 海巡機器人...")
    for keyword in KEYWORDS:
        posts = scrape_threads(keyword, max_posts=2)
        print(f" 🤖 成功抓取 {len(posts)} 篇最新貼文，開始交給 Gemini 分析...")
        for post in posts:
            analyze_and_notify(post)
            time.sleep(5)
    print(" ✅ 本次海巡任務圓滿結束！")
