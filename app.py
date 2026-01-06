import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import gspread
import io
import google.generativeai as genai
from datetime import datetime, timedelta

# --- 設定 ---
SHEET_NAME = "MyPortfolio"  # 你的 Google Sheet 名稱

# --- 0. 核心功能：台股代號對照表 (翻譯蒟蒻) ---
@st.cache_data(ttl=86400) # 快取 24 小時，避免重複爬蟲
def get_tw_stock_map():
    """從網路抓取台股清單，建立 '中文 -> 代號' 的對照表"""
    stock_map = {}
    try:
        # 抓上市
        url_twse = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
        df_twse = pd.read_html(url_twse, encoding='cp950')[0]
        df_twse = df_twse.iloc[2:] # 去掉標頭
        
        for index, row in df_twse.iterrows():
            code_name = str(row[0])
            # 格式通常是 "2330 台積電"，我們把它拆開
            if len(code_name.split()) >= 2:
                parts = code_name.split()
                code = parts[0]
                name = parts[1]
                # 建立對照
                stock_map[name] = f"{code}.TW" # 輸入 "台積電"
                stock_map[code] = f"{code}.TW" # 輸入 "2330"
        
        # 手動補充熱門美股與幣圈 (因為爬蟲抓不到這些)
        custom_map = {
            "NVDA": "NVDA", "輝達": "NVDA",
            "AAPL": "AAPL", "蘋果": "AAPL", 
            "TSLA": "TSLA", "特斯拉": "TSLA",
            "ETH": "ETH-USD", "以太幣": "ETH-USD", "ETH-USD": "ETH-USD",
            "BTC": "BTC-USD", "比特幣": "BTC-USD", "BTC-USD": "BTC-USD"
        }
        stock_map.update(custom_map)
        return stock_map
    except Exception as e:
        print(f"抓取清單失敗: {e}") # 印在後台
        return {}

# --- 1. 連接 Google Sheets ---
def get_google_sheet_connection():
    try:
        credentials = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials)
        sh = gc.open(SHEET_NAME)
        return sh.sheet1
    except Exception as e:
        st.error(f"連線 Google Sheets 失敗: {e}")
        st.stop()

def load_portfolio():
    worksheet = get_google_sheet_connection()
    try:
        data = worksheet.get_all_records()
        if not data:
            return pd.DataFrame(columns=['Ticker', 'Type', 'Shares', 'Avg_Cost'])
        df = pd.DataFrame(data)
        
        # 欄位防呆
        required_cols = ['Ticker', 'Type', 'Shares', 'Avg_Cost']
        for col in required_cols:
            if col not in df.columns:
                df[col] = 0.0 if col == 'Avg_Cost' else ""
        
        # ★ 重要資料清洗：讀取時把單引號拿掉
        # Google Sheet 裡存的是 "'2330.TW"，讀出來要變回 "2330.TW"
        df['Ticker'] = df['Ticker'].astype(str).str.replace("'", "")
        
        return df
    except gspread.exceptions.WorksheetNotFound:
        st.error("找不到工作表")
        st.stop()

def save_portfolio(df):
    worksheet = get_google_sheet_connection()
    worksheet.clear()
    
    # ★ 重要優化：寫入時加上單引號，防止 Google Sheet 變成超連結
    # 先複製一份以免影響當下顯示
    df_save = df.copy()
    df_save['Ticker'] = df_save['Ticker'].astype(str).apply(lambda x: f"'{x}" if x and not x.startswith("'") else x)
    
    # 寫入
    worksheet.update([df_save.columns.values.tolist()] + df_save.values.tolist())

# --- 2. AI 分析核心 ---
def ask_gemini_analysis(df_display):
    try:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        data_str = df_display.to_markdown(index=False)
        prompt = f"""
        你是一位專業財務顧問。請分析以下投資組合(TWD)：
        {data_str}
        請給出：1.資產配置評語 2.風險警告(集中度/波動) 3.具體行動建議。
        請用條列式，語氣專業。
        """
        # 使用你指定的最新模型
        model = genai.GenerativeModel('gemini-3-flash-preview') 
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI 發生錯誤: {e}"

# --- Excel 匯入 (維持簡化版) ---
def process_uploaded_file(uploaded_file):
    try:
        df_new = pd.read_excel(uploaded_file)
        # 簡易清洗邏輯 (略，與之前相同)
        return df_new, "Success"
    except Exception as e:
        return None, str(e)

# --- 主程式頁面 UI ---
st.set_page_config(page_title="Smart Asset Tracker", layout="wide", page_icon="📈")
st.title("📈 智慧資產追蹤 (全功能版)")

# 初始化：載入股票對照表
with st.spinner("正在更新股票清單..."):
    stock_map = get_tw_stock_map()

# 側邊欄
with st.sidebar:
    st.header("功能選單")
    mode = st.radio("", ["📊 資產總覽", "📝 智慧交易輸入", "📂 資料管理"])
    st.divider()
    bank_balance = st.number_input("銀行現金餘額", value=150000, step=1000)
    monthly_expense = st.number_input("本月累積花費", value=12000, step=500)

# 讀取資料
try:
    df_portfolio = load_portfolio()
except:
    df_portfolio = pd.DataFrame()

# --- 模式 A: 智慧交易輸入 (你的新需求) ---
if mode == "📝 智慧交易輸入":
    st.subheader("新增交易 (支援中文搜尋)")
    
    col1, col2 = st.columns([2, 1])
    
    detected_ticker = None
    
    with col1:
        # 1. 搜尋框
        user_input = st.text_input("輸入股票名稱或代號 (例如: 玉山金, 2330, NVDA)", placeholder="試試看輸入：玉山金")
        
        # 2. 辨識邏輯
        if user_input:
            clean_input = user_input.strip()
            # 查表
            if clean_input in stock_map:
                detected_ticker = stock_map[clean_input]
            # 判斷是否為台股代號 (4碼數字)
            elif clean_input.isdigit() and len(clean_input) == 4:
                detected_ticker = f"{clean_input}.TW"
            # 判斷是否為美股/Crypto (英文)
            else:
                detected_ticker = clean_input.upper()
                
            st.info(f"🔍 辨識代號: **{detected_ticker}**")

    # 3. K線圖預覽
    if detected_ticker:
        with st.spinner(f"正在抓取 {detected_ticker} 走勢圖..."):
            try:
                # 抓 3 個月資料
                chart_data = yf.download(detected_ticker, period="3mo", progress=False)
                
                if not chart_data.empty:
                    # 處理 MultiIndex (yfinance 新版問題)
                    if isinstance(chart_data.columns, pd.MultiIndex):
                        chart_data.columns = chart_data.columns.get_level_values(0)

                    fig = go.Figure(data=[go.Candlestick(
                        x=chart_data.index,
                        open=chart_data['Open'],
                        high=chart_data['High'],
                        low=chart_data['Low'],
                        close=chart_data['Close']
                    )])
                    fig.update_layout(title=f"{detected_ticker} 近三個月走勢", height=350, margin=dict(l=20, r=20, t=30, b=20))
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning(f"⚠️ 找不到 {detected_ticker} 的資料，請確認代號是否正確。")
                    detected_ticker = None
            except Exception as e:
                st.error(f"繪圖失敗: {e}")

    # 4. 確認與送出
    with st.form("trade_form"):
        st.write("---")
        st.write("#### 確認交易細節")
        c1, c2, c3 = st.columns(3)
        with c1:
            # 這裡自動帶入辨識出的代號
            final_ticker = st.text_input("確認代號", value=detected_ticker if detected_ticker else "")
        with c2:
            asset_type = st.selectbox("資產類型", ['TW Stock', 'US Stock', 'Crypto'])
        with c3:
            price = st.number_input("成交單價", min_value=0.0)
        
        shares = st.number_input("交易數量 (+買進 / -賣出)", step=0.01)
        
        if st.form_submit_button("送出交易"):
            if final_ticker and shares != 0:
                final_ticker = final_ticker.strip().upper()
                
                # 載入 -> 新增 -> 儲存
                df = load_portfolio()
                new_row = pd.DataFrame({'Ticker': [final_ticker], 'Type': [asset_type], 'Shares': [shares], 'Avg_Cost': [price]})
                df = pd.concat([df, new_row], ignore_index=True)
                
                save_portfolio(df) # 這裡會自動處理單引號
                st.success(f"✅ 已寫入: {final_ticker}")
                st.rerun()
            else:
                st.error("請等待圖表顯示或輸入完整資訊")

# --- 模式 B: 資產總覽 ---
elif mode == "📊 資產總覽":
    if not df_portfolio.empty:
        tickers = df_portfolio['Ticker'].tolist() + ["TWD=X"]
        
        with st.spinner('更新最新報價...'):
            try:
                # 批次抓取
                market_data = yf.download(tickers, period="1d", progress=False)['Close']
                # 處理 yfinance 新版格式問題
                if isinstance(market_data.columns, pd.MultiIndex): 
                    market_data.columns = market_data.columns.get_level_values(0)
                
                current_prices = market_data.iloc[-1]
                usdtwd = current_prices.get('TWD=X', 32.5) # 預設防呆

                results = []
                for index, row in df_portfolio.iterrows():
                    ticker = str(row['Ticker'])
                    shares = float(row['Shares'])
                    avg_cost = float(row['Avg_Cost'])
                    
                    price = current_prices.get(ticker, 0)
                    rate = usdtwd if row['Type'] in ['US Stock', 'Crypto'] else 1.0
                    
                    mkt_val = price * shares * rate
                    cost = avg_cost * shares * rate
                    pl = mkt_val - cost
                    roi = (pl/cost)*100 if cost!=0 else 0
                    
                    results.append({'代號': ticker, '類型': row['Type'], '市值': int(mkt_val), '損益': int(pl), '報酬率': roi})
                
                df_res = pd.DataFrame(results)
                
                # KPI
                total = df_res['市值'].sum() + bank_balance - monthly_expense
                pl_total = df_res['損益'].sum()
                
                k1, k2, k3 = st.columns(3)
                k1.metric("總資產", f"${total:,.0f}")
                k2.metric("總損益", f"${pl_total:+,.0f}")
                k3.metric("現金水位", f"${(bank_balance-monthly_expense):,.0f}")
                
                st.dataframe(df_res.style.format({'市值': "{:,}", '損益': "{:+}", '報酬率': "{:+.2f}%"}))
                
                # AI 按鈕
                st.markdown("---")
                if st.button("🤖 讓 Gemini 分析資產配置", type="primary"):
                    with st.spinner("AI 分析中..."):
                        ai_df = df_res[['代號', '類型', '市值', '報酬率']]
                        st.markdown(ask_gemini_analysis(ai_df))

            except Exception as e:
                st.error(f"報價錯誤: {e}")
    else:
        st.info("尚無資料，請到「智慧交易輸入」新增。")

# --- 模式 C: 資料管理 ---
elif mode == "📂 資料管理":
    st.info("若需批次匯入 Excel，請使用 v5 版本代碼，或直接在「智慧交易」逐筆輸入。")
    # 如果你很需要 Excel 匯入，我可以再把那段加回來，但目前建議先測試新功能
