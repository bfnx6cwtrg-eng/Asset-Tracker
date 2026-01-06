import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
import gspread
import io
import google.generativeai as genai

# --- 設定 ---
SHEET_NAME = "MyPortfolio"  # 你的 Google Sheet 名稱

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
    """從 Google Sheet 讀取資料"""
    worksheet = get_google_sheet_connection()
    try:
        data = worksheet.get_all_records()
        if not data:
            return pd.DataFrame(columns=['Ticker', 'Type', 'Shares', 'Avg_Cost'])
        df = pd.DataFrame(data)
        
        required_cols = ['Ticker', 'Type', 'Shares', 'Avg_Cost']
        for col in required_cols:
            if col not in df.columns:
                df[col] = 0.0 if col == 'Avg_Cost' else ""
                
        return df
    except gspread.exceptions.WorksheetNotFound:
        st.error("找不到工作表，請確認 Google Sheet 名稱正確")
        st.stop()

# --- 2. AI 分析核心 (修正：移到最外層) ---
def ask_gemini_analysis(df_display):
    """將整理好的資產數據傳給 Gemini 做分析"""
    # 1. 設定 API Key
    try:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    except:
        st.error("找不到 GEMINI_API_KEY，請檢查 Secrets 設定。")
        return None

    # 2. 準備提示詞
    data_str = df_display.to_markdown(index=False)
    
    prompt = f"""
    你現在是一位專業的財務顧問與資產管理專家。
    以下是用戶目前的投資組合數據（貨幣單位：TWD）：
    
    {data_str}
    
    請針對這個投資組合進行分析，請包含以下幾點：
    1. **資產配置評評**：股票、加密貨幣、現金的比例是否健康？
    2. **風險警告**：是否有單一標的佔比過重（Concentration Risk）？或是波動度過大的問題？
    3. **行動建議**：基於分散風險原則，下一步建議怎麼做？（例如：再平衡、獲利了結或是加碼）
    
    請用條列式回答，語氣專業但親切，並直接指出盲點。
    """

    # 3. 呼叫模型 (修正：使用正確的模型名稱)
    try:
        model = genai.GenerativeModel('gemini-1.5-flash') 
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI 思考時發生錯誤: {e}"

def save_portfolio(df):
    """將 DataFrame 寫回 Google Sheet"""
    worksheet = get_google_sheet_connection()
    worksheet.clear()
    worksheet.update([df.columns.values.tolist()] + df.values.tolist())

# --- Excel 匯入處理 ---
def process_uploaded_file(uploaded_file):
    try:
        df_new = pd.read_excel(uploaded_file)
        required_cols = ['Ticker', 'Type', 'Shares', 'Avg_Cost']
        if not all(col in df_new.columns for col in required_cols):
            return None, f"格式錯誤！缺少欄位: {required_cols}"
            
        def fix_ticker(row):
            ticker = str(row['Ticker']).strip().upper()
            asset_type = row['Type']
            if asset_type == 'TW Stock' and not ticker.endswith('.TW'):
                return f"{ticker}.TW"
            elif asset_type == 'Crypto' and not ticker.endswith('-USD'):
                 if not ticker.endswith('-USD'): return f"{ticker}-USD"
            return ticker

        df_new['Ticker'] = df_new.apply(fix_ticker, axis=1)
        df_new = df_new[df_new['Shares'] > 0]
        return df_new, "Success"
    except Exception as e:
        return None, str(e)

# --- 頁面 UI ---
st.set_page_config(page_title="Asset Tracker (AI版)", layout="wide", page_icon="💰")
st.title("💰 資產損益戰情室 (AI 賦能版)")

with st.sidebar:
    mode = st.radio("功能選單", ["📊 資產總覽", "📂 資料管理", "📝 單筆輸入"])
    st.divider()
    bank_balance = st.number_input("銀行現金餘額", value=150000, step=1000)
    monthly_expense = st.number_input("本月花費", value=12000, step=500)

# 讀取資料
try:
    df_portfolio = load_portfolio()
except Exception as e:
    st.warning("⚠️ 無法讀取資料，請檢查 Google Sheets 設定。")
    df_portfolio = pd.DataFrame()

# --- 邏輯：初始化 ---
if df_portfolio.empty and mode == "📊 資產總覽":
    st.info("目前資料庫為空。")
    if st.button("🚀 寫入範例資料"):
        demo_data = pd.DataFrame({
            'Ticker': ['2330.TW', 'NVDA', 'ETH-USD'],
            'Type': ['TW Stock', 'US Stock', 'Crypto'],
            'Shares': [2000, 10, 5.5],
            'Avg_Cost': [600, 120, 2500]
        })
        save_portfolio(demo_data)
        st.rerun()

# --- 模式 A: 資料管理 ---
elif mode == "📂 資料管理":
    st.subheader("批次匯入")
    sample_data = pd.DataFrame({'Ticker': ['2330', 'NVDA'], 'Type': ['TW Stock', 'US Stock'], 'Shares': [1000, 10], 'Avg_Cost': [500, 120]})
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        sample_data.to_excel(writer, index=False)
    st.download_button("📥 下載範本", data=buffer.getvalue(), file_name="template.xlsx")
    
    uploaded_file = st.file_uploader("上傳 Excel", type=['xlsx'])
    if uploaded_file:
        new_df, msg = process_uploaded_file(uploaded_file)
        if new_df is not None:
            st.dataframe(new_df)
            if st.button("🚨 確認覆蓋 Google Sheet"):
                save_portfolio(new_df)
                st.success("✅ 雲端資料已更新！")
                st.rerun()
        else:
            st.error(msg)

# --- 模式 B: 單筆輸入 ---
elif mode == "📝 單筆輸入":
    st.subheader("單筆交易")
    with st.form("trade_form"):
        col1, col2 = st.columns(2)
        with col1:
            asset_type = st.selectbox("資產類型", ['TW Stock', 'US Stock', 'Crypto'])
            t = st.text_input("代號")
        with col2:
            p = st.number_input("成交單價", min_value=0.0)
            s = st.number_input("數量", step=0.01)
            
        if st.form_submit_button("送出"):
            if t and s!=0:
                t = t.upper().strip()
                if asset_type == 'TW Stock' and not t.endswith('.TW'): t += '.TW'
                elif asset_type == 'Crypto' and not t.endswith('-USD'): t += '-USD'
                
                df = load_portfolio()
                new_row = pd.DataFrame({'Ticker': [t], 'Type': [asset_type], 'Shares': [s], 'Avg_Cost': [p]})
                df = pd.concat([df, new_row], ignore_index=True)
                save_portfolio(df)
                st.success(f"✅ 已寫入雲端: {t}")
                st.rerun()

# --- 模式 C: 資產總覽 ---
elif mode == "📊 資產總覽":
    if not df_portfolio.empty:
        tickers = df_portfolio['Ticker'].tolist() + ["TWD=X"]
        with st.spinner('連線 Yahoo Finance...'):
            try:
                market_data = yf.download(tickers, period="1d")['Close'].iloc[-1]
                usdtwd = market_data['TWD=X']
                results = []
                for index, row in df_portfolio.iterrows():
                    ticker = row['Ticker']
                    shares = float(row['Shares'])
                    avg_cost = float(row['Avg_Cost'])
                    current_price = market_data.get(ticker, 0)
                    rate = usdtwd if row['Type'] in ['US Stock', 'Crypto'] else 1.0
                    mkt_val = current_price * shares * rate
                    cost_basis = avg_cost * shares * rate
                    pl = mkt_val - cost_basis
                    roi = (pl/cost_basis)*100 if cost_basis!=0 else 0
                    results.append({'代號': ticker, '類型': row['Type'], '市值': mkt_val, '損益': pl, '報酬率': roi})
                
                df_res = pd.DataFrame(results)
                
                # KPI
                total_assets = df_res['市值'].sum() + bank_balance - monthly_expense
                st.metric("總資產", f"${total_assets:,.0f}")
                
                # 顯示表格
                st.dataframe(df_res.style.format({'市值': "{:,.0f}", '損益': "{:+,.0f}", '報酬率': "{:+.2f}%"}))

                st.markdown("---")
                
                # --- AI 分析區塊 ---
                st.subheader("🤖 AI 投資組合診斷")
                if st.button("讓 Gemini 分析我的資產配置 ✨", type="primary"):
                    with st.spinner("Gemini 正在分析您的持倉風險與機會..."):
                        # 準備數據 (只取重要欄位)
                        ai_df = df_res[['代號', '類型', '市值', '報酬率']].copy()
                        ai_df['市值'] = ai_df['市值'].apply(lambda x: int(x))
                        
                        # 呼叫 AI
                        analysis_result = ask_gemini_analysis(ai_df)
                        
                        if analysis_result:
                            st.success("分析完成！")
                            st.markdown(analysis_result)
                
                st.markdown("---")
                
                # 這裡可以補回你的圓餅圖代碼
                fig = px.pie(df_res, values='市值', names='類型', title='資產配置')
                st.plotly_chart(fig)
                
            except Exception as e:
                st.error(f"執行失敗: {e}")
