import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
import gspread
import io

# --- 設定 ---
SHEET_NAME = "MyPortfolio"  # 你的 Google Sheet 名稱

# --- 1. 連接 Google Sheets (取代原本的 load/save CSV) ---
def get_google_sheet_connection():
    # 從 Streamlit Secrets 讀取憑證
    try:
        credentials = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials)
        sh = gc.open(SHEET_NAME)
        return sh.sheet1 # 預設讀取第一張工作表
    except Exception as e:
        st.error(f"連線 Google Sheets 失敗: {e}")
        st.stop()

def load_portfolio():
    """從 Google Sheet 讀取資料"""
    worksheet = get_google_sheet_connection()
    try:
        data = worksheet.get_all_records() # 讀取所有資料為 List of Dicts
        if not data:
            return pd.DataFrame(columns=['Ticker', 'Type', 'Shares', 'Avg_Cost'])
        df = pd.DataFrame(data)
        
        # 確保欄位存在 (防呆)
        required_cols = ['Ticker', 'Type', 'Shares', 'Avg_Cost']
        for col in required_cols:
            if col not in df.columns:
                df[col] = 0.0 if col == 'Avg_Cost' else ""
                
        return df
    except gspread.exceptions.WorksheetNotFound:
        st.error("找不到工作表，請確認 Google Sheet 名稱正確")
        st.stop()

def save_portfolio(df):
    """將 DataFrame 寫回 Google Sheet"""
    worksheet = get_google_sheet_connection()
    # gspread 需要將 DataFrame 轉為 List of Lists
    # 1. 清空舊資料
    worksheet.clear()
    # 2. 寫入新資料 (包含標題)
    worksheet.update([df.columns.values.tolist()] + df.values.tolist())

# --- Excel 匯入處理 (不變) ---
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
st.set_page_config(page_title="Asset Tracker (Cloud)", layout="wide", page_icon="💰")
st.title("💰 資產損益戰情室 (Google Sheets 版)")

with st.sidebar:
    mode = st.radio("功能選單", ["📊 資產總覽", "📂 資料管理", "📝 單筆輸入"])
    st.divider()
    bank_balance = st.number_input("銀行現金餘額", value=150000, step=1000)
    monthly_expense = st.number_input("本月花費", value=12000, step=500)

# 讀取資料 (現在會去抓 Google Sheets)
try:
    df_portfolio = load_portfolio()
except Exception as e:
    st.warning("⚠️ 無法讀取資料，請檢查 Google Sheets 設定。")
    df_portfolio = pd.DataFrame()

# --- 邏輯：初始化 ---
if df_portfolio.empty and mode == "📊 資產總覽":
    st.info("目前資料庫為空。")
    if st.button("🚀 寫入範例資料到 Google Sheet"):
        demo_data = pd.DataFrame({
            'Ticker': ['2330.TW', 'NVDA', 'ETH-USD'],
            'Type': ['TW Stock', 'US Stock', 'Crypto'],
            'Shares': [2000, 10, 5.5],
            'Avg_Cost': [600, 120, 2500]
        })
        save_portfolio(demo_data)
        st.success("寫入成功！請重新整理頁面。")
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
                save_portfolio(df) # 寫回 Google Sheet
                st.success(f"✅ 已寫入雲端: {t}")
                st.rerun()

# --- 模式 C: 資產總覽 (跟之前一樣，省略重複部分但需保留完整邏輯) ---
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
                # 這裡放原本的 KPI 和圖表代碼...
                st.metric("總資產", f"${df_res['市值'].sum() + bank_balance - monthly_expense:,.0f}")
                st.dataframe(df_res.style.format({'市值': "{:,.0f}", '損益': "{:+,.0f}", '報酬率': "{:+.2f}%"}))
                
            except Exception as e:
                st.error(f"抓價失敗: {e}")
