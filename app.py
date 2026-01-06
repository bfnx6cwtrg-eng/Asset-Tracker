import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import gspread
import google.generativeai as genai
import json
import time # 用來控制 API 速度

# --- 設定 ---
SHEET_NAME = "MyPortfolio"

# --- 1. 連接 Google Sheets & Gemini ---
def get_google_sheet_connection():
    try:
        credentials = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials)
        sh = gc.open(SHEET_NAME)
        return sh.sheet1
    except Exception as e:
        st.error(f"連線 Google Sheets 失敗: {e}")
        st.stop()

def get_gemini_model():
    try:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        return genai.GenerativeModel('gemini-1.5-flash')
    except:
        st.error("Secrets 設定錯誤：找不到 GEMINI_API_KEY")
        st.stop()

# --- 2. 核心：AI 股票識別 (通用版) ---
@st.cache_data(ttl=3600)
def identify_stock_with_ai(query):
    """輸入中文名，回傳標準代號"""
    if not query or query == "nan": return None
    
    # 簡單防呆：如果是 4 碼數字，直接當作台股
    if str(query).isdigit() and len(str(query)) == 4:
        return {"ticker": f"{query}.TW", "name": str(query), "type": "TW Stock"}

    model = get_gemini_model()
    prompt = f"""
    Identify the financial asset: "{query}"
    Return JSON with keys: "ticker" (Yahoo Finance format, e.g. 2330.TW), "name", "type".
    If unsure, return null.
    """
    try:
        response = model.generate_content(prompt)
        text = response.text.strip().replace("```json", "").replace("```", "")
        if "null" in text: return None
        return json.loads(text)
    except:
        return None

# --- 3. 資料庫操作 ---
def load_portfolio():
    worksheet = get_google_sheet_connection()
    try:
        data = worksheet.get_all_records()
        if not data: return pd.DataFrame(columns=['Ticker', 'Type', 'Shares', 'Avg_Cost'])
        df = pd.DataFrame(data)
        if 'Ticker' in df.columns:
            df['Ticker'] = df['Ticker'].astype(str).str.replace("'", "")
        return df
    except:
        return pd.DataFrame()

def save_portfolio(df):
    worksheet = get_google_sheet_connection()
    worksheet.clear()
    df_save = df.copy()
    df_save['Ticker'] = df_save['Ticker'].astype(str).apply(lambda x: f"'{x}" if x and not x.startswith("'") else x)
    worksheet.update([df_save.columns.values.tolist()] + df_save.values.tolist())

# --- 4. 頁面 UI ---
st.set_page_config(page_title="AI Asset Tracker", layout="wide", page_icon="🤖")
st.title("🤖 AI 智慧資產追蹤")

with st.sidebar:
    mode = st.radio("選單", ["📊 資產總覽", "📝 智慧交易", "📂 資料管理"])
    st.divider()
    bank_balance = st.number_input("銀行餘額", value=150000, step=1000)
    monthly_expense = st.number_input("本月花費", value=12000, step=500)

df_portfolio = load_portfolio()

# --- 模式 A: 智慧交易 ---
if mode == "📝 智慧交易":
    st.subheader("新增交易")
    query = st.text_input("輸入名稱或代號", placeholder="例如：玉山金, 2330, AAPL")
    
    found_ticker, found_name, found_type, current_price = None, "", "TW Stock", 0.0

    if query:
        with st.spinner("AI 識別中..."):
            ai_result = identify_stock_with_ai(query)
            if ai_result:
                found_ticker = ai_result['ticker']
                found_name = ai_result['name']
                found_type = ai_result['type']
                try:
                    stock = yf.Ticker(found_ticker)
                    hist = stock.history(period="1d")
                    if not hist.empty: current_price = float(hist['Close'].iloc[-1])
                    st.success(f"✅ 識別為：{found_name} ({found_ticker}) | 現價：{current_price}")
                except:
                    st.warning("識別成功但抓不到價格")
            else:
                st.error("AI 無法識別")

    with st.form("trade_form"):
        c1, c2, c3 = st.columns(3)
        with c1: final_ticker = st.text_input("代號", value=found_ticker if found_ticker else "")
        with c2: asset_type = st.selectbox("類型", ['TW Stock', 'US Stock', 'Crypto'], index=0)
        with c3: input_price = st.number_input("單價", value=current_price)
        shares = st.number_input("數量", step=0.01)
        
        if st.form_submit_button("送出"):
            if final_ticker and shares != 0:
                df = load_portfolio()
                new_row = pd.DataFrame({'Ticker': [final_ticker], 'Type': [asset_type], 'Shares': [shares], 'Avg_Cost': [input_price]})
                df = pd.concat([df, new_row], ignore_index=True)
                save_portfolio(df)
                st.toast("已儲存")
                st.rerun()

# --- 模式 B: 資產總覽 ---
elif mode == "📊 資產總覽":
    if not df_portfolio.empty:
        tickers = df_portfolio['Ticker'].tolist() + ["TWD=X"]
        with st.spinner('更新報價...'):
            try:
                market_data = yf.download(tickers, period="5d", progress=False)['Close']
                if isinstance(market_data.columns, pd.MultiIndex): market_data.columns = market_data.columns.get_level_values(0)
                prices = market_data.ffill().iloc[-1]
                usdtwd = prices.get('TWD=X', 32.5)

                results = []
                for idx, row in df_portfolio.iterrows():
                    t = str(row['Ticker'])
                    # 檢查：如果代號含有中文 (代表還沒修復)，直接跳過或給 0
                    price = 0
                    if t in prices:
                        price = prices[t]
                    elif any("\u4e00" <= char <= "\u9fff" for char in t):
                         st.warning(f"⚠️ 發現無效代號：'{t}'，請去「📂 資料管理」進行 AI 修復。")
                    
                    val = price * float(row['Shares']) * (usdtwd if row['Type'] != 'TW Stock' else 1)
                    results.append({'代號': t, '市值': int(val), '損益': int(val - float(row['Avg_Cost'])*float(row['Shares'])*(usdtwd if row['Type']!='TW Stock' else 1))})
                
                df_res = pd.DataFrame(results)
                st.metric("總資產", f"${df_res['市值'].sum() + bank_balance - monthly_expense:,.0f}")
                st.dataframe(df_res)
                
                if st.button("🤖 AI 分析"):
                    model = get_gemini_model()
                    st.write(model.generate_content(f"分析: {df_res.to_markdown()}").text)

            except Exception as e:
                st.error(f"錯誤: {e}")

# --- 模式 C: 資料管理 (新增修復功能) ---
elif mode == "📂 資料管理":
    st.subheader("🔧 資料庫維護")
    st.info("如果你直接在 Google Sheets 貼上了中文名稱（如「玉山金」），請按下方按鈕來修復。")
    
    st.dataframe(df_portfolio)

    if st.button("🛠️ 掃描並用 AI 修復代號", type="primary"):
        progress_bar = st.progress(0)
        log_text = st.empty()
        
        updates_made = False
        df_new = df_portfolio.copy()
        
        for index, row in df_new.iterrows():
            ticker = str(row['Ticker'])
            # 判斷邏輯：如果有中文字，或是看起來不像代號
            has_chinese = any("\u4e00" <= char <= "\u9fff" for char in ticker)
            
            if has_chinese:
                log_text.write(f"正在修復：{ticker} ...")
                ai_res = identify_stock_with_ai(ticker)
                
                if ai_res and ai_res['ticker']:
                    new_ticker = ai_res['ticker']
                    df_new.at[index, 'Ticker'] = new_ticker
                    # 順便修正類型
                    if ai_res['type']: df_new.at[index, 'Type'] = ai_res['type']
                    
                    st.toast(f"✅ 修復成功：{ticker} -> {new_ticker}")
                    updates_made = True
                else:
                    log_text.write(f"❌ AI 無法識別：{ticker}")
                
                time.sleep(1) # 避免 API 衝太快
            
            progress_bar.progress((index + 1) / len(df_new))

        if updates_made:
            save_portfolio(df_new)
            st.success("🎉 所有中文名稱已轉換為標準代號！")
            st.rerun()
        else:
            st.info("檢查完畢，沒有發現需要修復的代號。")
