import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import gspread
import google.generativeai as genai
import json

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
        return genai.GenerativeModel('gemini-3-flash-preview') # 用 Flash 處理這種小任務最快
    except:
        st.error("Secrets 設定錯誤：找不到 GEMINI_API_KEY")
        st.stop()

# --- 2. 核心：AI 股票識別員 (取代舊的爬蟲) ---
@st.cache_data(ttl=3600) # 快取 1 小時，同樣的搜尋不用一直問 AI
def identify_stock_with_ai(query):
    """
    輸入：玉山 / 2330 / NVDA
    輸出：{'ticker': '2884.TW', 'name': '玉山金', 'type': 'TW Stock'}
    """
    model = get_gemini_model()
    prompt = f"""
    User Input: "{query}"
    
    Task: Identify the financial asset based on the input.
    Target Markets: Taiwan Stocks (TW), US Stocks, Cryptocurrencies.
    
    Return a pure JSON object with these keys:
    - "ticker": The Yahoo Finance ticker symbol (e.g., "2330.TW", "NVDA", "ETH-USD").
    - "name": The common company/asset name in Traditional Chinese (e.g., "台積電", "輝達", "以太幣").
    - "type": One of ["TW Stock", "US Stock", "Crypto"].
    
    Rules:
    - If user inputs a 4-digit number (e.g. 2884), assume Taiwan Stock (add .TW).
    - If input is ambiguous but looks like a company name, output the most likely stock.
    - If input is unrecognizable, return "null".
    - Do NOT output markdown formatting (like ```json), just the raw JSON string.
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
        # 清洗 Ticker 防止單引號問題
        if 'Ticker' in df.columns:
            df['Ticker'] = df['Ticker'].astype(str).str.replace("'", "")
        return df
    except:
        return pd.DataFrame()

def save_portfolio(df):
    worksheet = get_google_sheet_connection()
    worksheet.clear()
    df_save = df.copy()
    # 寫入時加單引號防止轉連結
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

# --- 模式 A: 智慧交易 (大幅升級) ---
if mode == "📝 智慧交易":
    st.subheader("新增交易")
    st.caption("✨ 支援模糊搜尋：試試輸入「玉山」、「鴻海」或「Bitcoin」")
    
    # 1. 搜尋輸入
    query = st.text_input("輸入名稱或代號", placeholder="例如：玉山金, 2330, AAPL")
    
    # 初始化變數
    found_ticker = None
    found_name = ""
    found_type = "TW Stock"
    current_price = 0.0

    # 2. AI 辨識 + yfinance 查價
    if query:
        with st.spinner("AI 正在識別並抓取即時報價..."):
            # Step A: 問 AI 這是什麼
            ai_result = identify_stock_with_ai(query)
            
            if ai_result:
                found_ticker = ai_result['ticker']
                found_name = ai_result['name']
                found_type = ai_result['type']
                
                # Step B: 問 yfinance 現在多少錢
                try:
                    stock = yf.Ticker(found_ticker)
                    # 嘗試取得最新價 (相容性寫法)
                    price_info = stock.fast_info
                    if hasattr(price_info, 'last_price') and price_info.last_price:
                        current_price = float(price_info.last_price)
                    else:
                         # 備用方案: 抓 1 天歷史
                        hist = stock.history(period="1d")
                        if not hist.empty:
                            current_price = float(hist['Close'].iloc[-1])
                    
                    # 顯示成功訊息
                    st.success(f"✅ 識別成功：**{found_name} ({found_ticker})** | 現價：**{current_price:,.2f}**")
                    
                    # 畫 K 線圖
                    hist_data = stock.history(period="3mo")
                    if not hist_data.empty:
                        fig = go.Figure(data=[go.Candlestick(
                            x=hist_data.index,
                            open=hist_data['Open'], high=hist_data['High'],
                            low=hist_data['Low'], close=hist_data['Close']
                        )])
                        fig.update_layout(title=f"{found_name} 近三個月走勢", height=300, margin=dict(l=20, r=20, t=30, b=20))
                        st.plotly_chart(fig, use_container_width=True)
                        
                except Exception as e:
                    st.warning(f"已識別代號 {found_ticker}，但抓取股價失敗。")
            else:
                st.error("AI 無法識別此資產，請嘗試輸入完整代號 (如 2330.TW)。")

    # 3. 交易表單 (自動帶入 AI 查到的資料)
    st.write("---")
    with st.form("trade_form"):
        c1, c2, c3 = st.columns(3)
        with c1:
            # 自動填入 Ticker
            final_ticker = st.text_input("確認代號", value=found_ticker if found_ticker else "")
        with c2:
            # 自動填入類型
            asset_type = st.selectbox("資產類型", ['TW Stock', 'US Stock', 'Crypto'], 
                                    index=['TW Stock', 'US Stock', 'Crypto'].index(found_type) if found_type in ['TW Stock', 'US Stock', 'Crypto'] else 0)
        with c3:
            # ★ 自動填入股價 (這是你要的功能!)
            input_price = st.number_input("成交單價", min_value=0.0, value=current_price, step=0.5)
        
        shares = st.number_input("數量 (+買 / -賣)", step=0.01)
        
        if st.form_submit_button("送出交易"):
            if final_ticker and shares != 0:
                df = load_portfolio()
                new_row = pd.DataFrame({'Ticker': [final_ticker], 'Type': [asset_type], 'Shares': [shares], 'Avg_Cost': [input_price]})
                df = pd.concat([df, new_row], ignore_index=True)
                save_portfolio(df)
                st.toast(f"🎉 交易已記錄：{found_name}")
                st.rerun()

# --- 模式 B: 資產總覽 (維持穩定版) ---
elif mode == "📊 資產總覽":
    if not df_portfolio.empty:
        tickers = df_portfolio['Ticker'].tolist() + ["TWD=X"]
        with st.spinner('更新報價中...'):
            try:
                # 抓 5 天防止 NaN
                market_data = yf.download(tickers, period="5d", progress=False)['Close']
                if isinstance(market_data.columns, pd.MultiIndex): market_data.columns = market_data.columns.get_level_values(0)
                current_prices = market_data.ffill().iloc[-1]
                usdtwd = current_prices.get('TWD=X', 32.5)

                results = []
                for index, row in df_portfolio.iterrows():
                    t = str(row['Ticker'])
                    s = float(row['Shares'])
                    c = float(row['Avg_Cost'])
                    # 價格防呆
                    p = current_prices.get(t, 0)
                    if pd.isna(p): p = 0
                    
                    rate = usdtwd if row['Type'] in ['US Stock', 'Crypto'] else 1.0
                    val = p * s * rate
                    cost = c * s * rate
                    pl = val - cost
                    roi = (pl/cost)*100 if cost!=0 else 0
                    
                    results.append({'代號': t, '類型': row['Type'], '市值': int(val), '損益': int(pl), '報酬率': roi})
                
                df_res = pd.DataFrame(results)
                
                # KPI
                total = df_res['市值'].sum() + bank_balance - monthly_expense
                pl_total = df_res['損益'].sum()
                
                col1, col2, col3 = st.columns(3)
                col1.metric("總資產", f"${total:,.0f}")
                col2.metric("總損益", f"${pl_total:+,.0f}")
                col3.metric("現金", f"${(bank_balance-monthly_expense):,.0f}")
                
                st.dataframe(df_res.style.format({'市值': "{:,}", '損益': "{:+}", '報酬率': "{:+.2f}%"}))
                
                # AI 分析
                st.markdown("---")
                if st.button("🤖 AI 投資診斷"):
                    with st.spinner("Gemini 思考中..."):
                        # AI 分析部分
                        model = get_gemini_model()
                        prompt = f"分析投資組合(TWD): {df_res[['代號','市值','報酬率']].to_markdown()}. 給出配置建議與風險。"
                        st.markdown(model.generate_content(prompt).text)

            except Exception as e:
                st.error(f"報價錯誤: {e}")
    else:
        st.info("尚無資料")

elif mode == "📂 資料管理":
    st.info("請直接使用「智慧交易」功能進行管理。")
