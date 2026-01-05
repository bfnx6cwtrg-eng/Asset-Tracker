import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
import os
import io

# --- 檔案設定 ---
CSV_FILE = 'portfolio.csv'

# --- 資料庫管理 ---
def load_portfolio():
    if not os.path.exists(CSV_FILE):
        return pd.DataFrame(columns=['Ticker', 'Type', 'Shares', 'Avg_Cost'])
    df = pd.read_csv(CSV_FILE)
    if 'Avg_Cost' not in df.columns:
        df['Avg_Cost'] = 0.0
    return df

def save_portfolio(df):
    df.to_csv(CSV_FILE, index=False)

# --- Excel 處理邏輯 ---
def process_uploaded_file(uploaded_file):
    try:
        df_new = pd.read_excel(uploaded_file)
        required_cols = ['Ticker', 'Type', 'Shares', 'Avg_Cost']
        # 簡易檢查
        if not all(col in df_new.columns for col in required_cols):
            return None, f"格式錯誤！缺少欄位: {required_cols}"
            
        # 資料清洗
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
st.set_page_config(page_title="Asset Tracker v5", layout="wide", page_icon="💰")
st.title("💰 資產損益戰情室 (Fix版)")

# --- 側邊欄 ---
with st.sidebar:
    st.warning("⚠️ 注意：此為 Colab 暫存環境，關閉視窗後資料將消失。")
    mode = st.radio("功能選單", ["📊 資產總覽", "📂 資料管理", "📝 單筆輸入"])
    
    st.divider()
    bank_balance = st.number_input("銀行現金餘額 (TWD)", value=150000, step=1000)
    monthly_expense = st.number_input("本月累積花費 (TWD)", value=12000, step=500)

df_portfolio = load_portfolio()

# --- 邏輯：如果是空的，顯示歡迎畫面與 Demo 按鈕 ---
if df_portfolio.empty and mode == "📊 資產總覽":
    st.info("👋 資料庫為空。請匯入資料或使用範例。")
    if st.button("🚀 載入範例資料 (Demo)", type="primary"):
        demo_data = pd.DataFrame({
            'Ticker': ['2330.TW', 'NVDA', 'ETH-USD', '0050.TW'],
            'Type': ['TW Stock', 'US Stock', 'Crypto', 'TW Stock'],
            'Shares': [2000, 10, 5.5, 1000],
            'Avg_Cost': [600, 120, 2500, 130]
        })
        save_portfolio(demo_data)
        st.rerun()

# --- 模式 A: 資料管理 ---
elif mode == "📂 資料管理":
    st.subheader("批次匯入 / 下載")
    
    # 範本下載
    sample_data = pd.DataFrame({
        'Ticker': ['2330', 'NVDA', 'ETH'],
        'Type': ['TW Stock', 'US Stock', 'Crypto'],
        'Shares': [1000, 10, 5.5],
        'Avg_Cost': [500, 120, 2500]
    })
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        sample_data.to_excel(writer, index=False)
    st.download_button("📥 下載 Excel 範本", data=buffer.getvalue(), file_name="portfolio_template.xlsx")
    
    uploaded_file = st.file_uploader("上傳 Excel", type=['xlsx'])
    if uploaded_file:
        new_df, msg = process_uploaded_file(uploaded_file)
        if new_df is not None:
            st.dataframe(new_df)
            if st.button("🚨 確認覆蓋資料庫"):
                save_portfolio(new_df)
                st.success("✅ 資料庫已更新！")
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
            t = st.text_input("代號 (Ex: 2330, BTC)")
        with col2:
            p = st.number_input("成交單價 (原幣)", min_value=0.0)
            s = st.number_input("數量 (+買 / -賣)", step=0.01)
            
        if st.form_submit_button("送出交易"):
            if t and s!=0:
                t = t.upper().strip()
                if asset_type == 'TW Stock' and not t.endswith('.TW'): t += '.TW'
                elif asset_type == 'Crypto' and not t.endswith('-USD'): t += '-USD'
                
                df = load_portfolio()
                # 簡單追加模式
                new_row = pd.DataFrame({'Ticker': [t], 'Type': [asset_type], 'Shares': [s], 'Avg_Cost': [p]})
                df = pd.concat([df, new_row], ignore_index=True)
                save_portfolio(df)
                st.success(f"已紀錄: {t}")
                st.rerun()

# --- 模式 C: 資產總覽 ---
elif mode == "📊 資產總覽":
    tickers = df_portfolio['Ticker'].tolist() + ["TWD=X"]
    
    with st.spinner('正在連線 Yahoo Finance...'):
        try:
            market_data = yf.download(tickers, period="1d")['Close'].iloc[-1]
            usdtwd = market_data['TWD=X']
            
            results = []
            for index, row in df_portfolio.iterrows():
                ticker = row['Ticker']
                shares = row['Shares']
                avg_cost = row['Avg_Cost']
                current_price = market_data.get(ticker, 0)
                
                is_foreign = row['Type'] in ['US Stock', 'Crypto']
                rate = usdtwd if is_foreign else 1.0
                
                mkt_val = current_price * shares * rate
                cost_basis = avg_cost * shares * rate
                pl = mkt_val - cost_basis
                roi = (pl/cost_basis)*100 if cost_basis!=0 else 0
                
                results.append({
                    '代號': ticker, 
                    '類型': row['Type'],
                    '持倉': shares,
                    '現價': current_price,
                    '平均成本': avg_cost,
                    '市值': mkt_val, 
                    '損益': pl, 
                    '報酬率': roi
                })
                
            df_res = pd.DataFrame(results)
            
            # --- 儀表板呈現區 ---
            invest_assets = df_res['市值'].sum()
            liquid_cash = bank_balance - monthly_expense
            total_assets = invest_assets + liquid_cash
            total_pl = df_res['損益'].sum()
            
            kpi1, kpi2, kpi3, kpi4 = st.columns(4)
            kpi1.metric("💎 總資產", f"${total_assets:,.0f}")
            kpi2.metric("📈 投資部位", f"${invest_assets:,.0f}")
            kpi3.metric("💵 流動現金", f"${liquid_cash:,.0f}")
            kpi4.metric("💰 總損益", f"${total_pl:+,.0f}", delta=f"{(total_pl/invest_assets)*100:.1f}%" if invest_assets>0 else "0%")
            
            st.divider()
            
            c1, c2 = st.columns([1.5, 1])
            with c1:
                st.subheader("持倉明細")
                # 這裡改用 Streamlit 原生表格，確保顯示正常
                st.dataframe(
                    df_res[['代號', '類型', '持倉', '市值', '損益', '報酬率']]
                    .style.format({'市值': "{:,.0f}", '損益': "{:+,.0f}", '報酬率': "{:+.2f}%"}),
                    use_container_width=True
                )
                
            with c2:
                st.subheader("資產配置")
                df_chart = df_res[['類型', '市值']].copy()
                new_row = pd.DataFrame({'類型': ['Cash'], '市值': [liquid_cash]})
                df_chart = pd.concat([df_chart, new_row], ignore_index=True)
                
                fig = px.pie(df_chart, values='市值', names='類型', hole=0.5)
                st.plotly_chart(fig, use_container_width=True)
                
        except Exception as e:
            st.error(f"抓取失敗: {e}")
