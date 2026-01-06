import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import gspread
import google.generativeai as genai
import json
import time
import re

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
        return genai.GenerativeModel('gemini-2.5-flash')
    except:
        st.error("Secrets 設定錯誤：找不到 GEMINI_API_KEY")
        st.stop()

# --- 2. 核心：AI 股票識別 (v9 強制 JSON 版) ---
@st.cache_data(ttl=3600)
def identify_stock_with_ai(query):
    """輸入中文名，回傳標準代號 (純 AI 處理)"""
    if not query or str(query).lower() == "nan": return None
    
    q = str(query).strip()
    # 基本防呆
    if q.isdigit() and len(q) == 4:
        return {"ticker": f"{q}.TW", "name": q, "type": "TW Stock"}

    model = get_gemini_model()
    
    # ★ Prompt 大升級：給範例 (Few-Shot) + 強制 JSON 結構
    prompt = f"""
    Role: You are a strict JSON data converter for financial tickers (Yahoo Finance).
    User Input: "{query}"
    
    Instructions:
    1. Identify the company or asset from the input.
    2. Convert it to the CORRECT Yahoo Finance ticker.
    3. **Taiwan Stocks**: Use 4 digits + ".TW" (e.g., 2330.TW).
    4. **Cryptocurrencies**: MUST end with "-USD" (e.g., ETH-USD, BTC-USD).
    5. **US Stocks**: Ticker only (e.g., NVDA, TSLA).
    
    Output Format:
    Return ONLY a single valid JSON object. Do NOT write "json" or markdown blocks.
    
    Examples:
    - Input: "台積電" -> {{"ticker": "2330.TW", "name": "TSMC", "type": "TW Stock"}}
    - Input: "以太幣" -> {{"ticker": "ETH-USD", "name": "Ethereum", "type": "Crypto"}}
    - Input: "台達電" -> {{"ticker": "2308.TW", "name": "Delta Electronics", "type": "TW Stock"}}
    """
    
    try:
        # 使用 temperature=0 降低 AI 的「創造力」(幻覺)，讓它更精準
        response = model.generate_content(prompt, generation_config=genai.types.GenerationConfig(temperature=0.0))
        text = response.text.strip()
        
        # 暴力清洗：只保留 { 到 } 之間的內容
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            json_str = match.group()
            return json.loads(json_str)
        else:
            # 如果失敗，回傳原始文字以便除錯
            return {"error": "format_error", "raw_text": text}
            
    except Exception as e:
        return {"error": "api_error", "msg": str(e)}
        
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
    # 寫入時加單引號
    df_save['Ticker'] = df_save['Ticker'].astype(str).apply(lambda x: f"'{x}" if x and not x.startswith("'") else x)
    worksheet.update([df_save.columns.values.tolist()] + df_save.values.tolist())

# --- 4. 頁面 UI ---
st.set_page_config(page_title="AI Asset Tracker", layout="wide", page_icon="🤖")
st.title("🤖 AI 智慧資產追蹤 (v8修復版)")

with st.sidebar:
    mode = st.radio("選單", ["📊 資產總覽", "📝 智慧交易", "📂 資料管理"])
    st.divider()
    bank_balance = st.number_input("銀行餘額", value=150000, step=1000)
    monthly_expense = st.number_input("本月花費", value=12000, step=500)

df_portfolio = load_portfolio()

# --- 模式 A: 智慧交易 (v9 自動救援版) ---
if mode == "📝 智慧交易":
    st.subheader("新增交易")
    st.caption("✨ 支援：以太幣、台達電、0050")
    
    query = st.text_input("輸入名稱或代號", placeholder="例如：台達電")
    
    found_ticker, found_name, found_type, current_price = None, "", "TW Stock", 0.0

    if query:
        with st.spinner("AI 識別與查價中..."):
            ai_result = identify_stock_with_ai(query)
            
            # 1. 檢查 AI 是否回傳錯誤
            if not ai_result:
                st.error("AI 罷工了，沒有回傳任何東西。")
            elif "error" in ai_result:
                st.error(f"AI 識別失敗。它回傳了：{ai_result.get('raw_text', '')}")
                st.caption("建議：試著輸入更精確的名稱")
            else:
                # AI 成功回傳 JSON
                ticker_candidate = ai_result['ticker']
                found_name = ai_result['name']
                found_type = ai_result['type']
                
                # ★ 2. 雙重市場偵測 (.TW vs .TWO)
                # 很多時候 AI 給 6510.TW (錯)，其實應該是 6510.TWO (對)
                # 我們建立一個測試清單，依序嘗試
                tickers_to_try = [ticker_candidate]
                if ticker_candidate.endswith(".TW"):
                    tickers_to_try.append(ticker_candidate.replace(".TW", ".TWO"))
                
                valid_ticker = None
                
                for t in tickers_to_try:
                    try:
                        stock = yf.Ticker(t)
                        # 試抓一天資料來驗證代號是否存在
                        hist = stock.history(period="1d")
                        if not hist.empty:
                            valid_ticker = t
                            current_price = float(hist['Close'].iloc[-1])
                            
                            # 畫圖
                            hist_3mo = stock.history(period="3mo")
                            fig = go.Figure(data=[go.Candlestick(
                                x=hist_3mo.index,
                                open=hist_3mo['Open'], high=hist_3mo['High'],
                                low=hist_3mo['Low'], close=hist_3mo['Close']
                            )])
                            fig.update_layout(title=f"{found_name} ({valid_ticker})", height=300, margin=dict(t=30, b=0, l=0, r=0))
                            st.plotly_chart(fig, use_container_width=True)
                            
                            st.success(f"✅ 成功鎖定：{found_name} ({valid_ticker}) | 現價：{current_price:.2f}")
                            break # 找到了就跳出迴圈
                    except:
                        continue # 這個代號失敗，試下一個
                
                if valid_ticker:
                    found_ticker = valid_ticker
                else:
                    st.warning(f"AI 建議代號 {ticker_candidate}，但 Yahoo Finance 抓不到資料。")
                    st.caption("可能原因：這是上櫃股票但 AI 給了上市代號，或是該代號已下市。")

    # 交易表單 (維持不變)
    st.write("---")
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

    # 交易表單
    st.write("---")
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

# --- 模式 B: 資產總覽 (修復欄位與合併) ---
elif mode == "📊 資產總覽":
    if not df_portfolio.empty:
        tickers = df_portfolio['Ticker'].unique().tolist() + ["TWD=X"]
        
        with st.spinner('計算資產中...'):
            try:
                # 抓報價
                market_data = yf.download(tickers, period="5d", progress=False)['Close']
                if isinstance(market_data.columns, pd.MultiIndex): market_data.columns = market_data.columns.get_level_values(0)
                prices = market_data.ffill().iloc[-1]
                usdtwd = prices.get('TWD=X', 32.5)

                # --- ★ 新增：合併相同股票邏輯 (Group By) ---
                # 先把 DataFrame 裡的數字轉型，以免計算錯誤
                df_portfolio['Shares'] = pd.to_numeric(df_portfolio['Shares'])
                df_portfolio['Avg_Cost'] = pd.to_numeric(df_portfolio['Avg_Cost'])
                
                # 計算總成本 (Cost Basis) = 股數 * 平均成本
                df_portfolio['Total_Cost_Basis'] = df_portfolio['Shares'] * df_portfolio['Avg_Cost']

                # 依照 Ticker 和 Type 分組加總
                grouped = df_portfolio.groupby(['Ticker', 'Type']).agg({
                    'Shares': 'sum',
                    'Total_Cost_Basis': 'sum'
                }).reset_index()

                # 算出新的加權平均成本
                grouped['Avg_Cost'] = grouped.apply(lambda x: x['Total_Cost_Basis'] / x['Shares'] if x['Shares']!=0 else 0, axis=1)

                results = []
                for idx, row in grouped.iterrows():
                    t = str(row['Ticker'])
                    s = float(row['Shares'])
                    c = float(row['Avg_Cost'])
                    
                    # 價格防呆
                    p = 0
                    if t in prices and not pd.isna(prices[t]):
                        p = prices[t]
                    elif any("\u4e00" <= char <= "\u9fff" for char in t):
                         st.warning(f"⚠️ 無效代號：'{t}'")
                    
                    rate = usdtwd if row['Type'] != 'TW Stock' else 1
                    
                    val = p * s * rate
                    cost_basis = c * s * rate
                    pl = val - cost_basis
                    roi = (pl/cost_basis)*100 if cost_basis!=0 else 0
                    
                    # ★ 這裡補回了「持倉」和「平均成本」
                    results.append({
                        '代號': t, 
                        '類型': row['Type'], 
                        '持倉': s,           # <--- 補回來了
                        '平均成本': int(c),   # <--- 補回來了
                        '現價': p,
                        '市值': int(val), 
                        '損益': int(pl), 
                        '報酬率': roi
                    })
                
                df_res = pd.DataFrame(results)
                
                # KPI
                total = df_res['市值'].sum() + bank_balance - monthly_expense
                pl_total = df_res['損益'].sum()
                
                c1, c2, c3 = st.columns(3)
                c1.metric("總資產", f"${total:,.0f}")
                c2.metric("總損益", f"${pl_total:+,.0f}")
                c3.metric("現金", f"${(bank_balance-monthly_expense):,.0f}")
                
                # 顯示表格 (調整順序)
                st.dataframe(
                    df_res[['代號', '類型', '持倉', '平均成本', '現價', '市值', '損益', '報酬率']]
                    .style.format({'市值': "{:,}", '損益': "{:+}", '報酬率': "{:+.2f}%", '持倉': "{:,.2f}", '平均成本': "{:,.0f}", '現價': "{:,.2f}"})
                )
                
                # AI 按鈕
                st.markdown("---")
                if st.button("🤖 AI 分析"):
                    model = get_gemini_model()
                    st.markdown(model.generate_content(f"分析: {df_res.to_markdown()}").text)

            except Exception as e:
                st.error(f"錯誤: {e}")
                st.write(e) # Debug info

# --- 模式 C: 資料管理 ---
elif mode == "📂 資料管理":
    st.subheader("🔧 資料庫維護")
    st.info("可在 Google Sheets 貼上中文名稱，再按下方修復。")
    st.dataframe(df_portfolio)
    if st.button("🛠️ AI 修復代號", type="primary"):
        # (這裡維持之前的邏輯，省略以節省篇幅)
        # 為了完整性，若你需要這段，請保留 v7 的這部分代碼
        pass 
        # 請確認上面 v7 的代碼有包含這塊，如果沒有我可以補上
