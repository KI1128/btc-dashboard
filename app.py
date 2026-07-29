import streamlit as st
import pandas as pd
from streamlit_autorefresh import st_autorefresh
import numpy as np
import requests
import os
from datetime import datetime, timezone
import plotly.graph_objects as go

# ==========================================
# 1. 設定 & 定数
# ==========================================
st.set_page_config(page_title="BTC Strategy Dashboard", layout="wide")

st.markdown("""
    <style>
        .block-container {
            padding-top: 1rem;
            padding-bottom: 0rem;
        }
    </style>
""", unsafe_allow_html=True)

# 60秒（60000ミリ秒）ごとに自動で画面をリロードする設定
st_autorefresh(interval=60000, limit=None, key="data_refresh")

CSV_FILE = 'btc_daily_dataset.csv'
SYMBOL = 'BTCUSDT'

# ==========================================
# 2. データ取得・更新ロジック (Binance API)
# ==========================================
def fetch_binance_klines(start_ts=None, limit=1000):
    """Binanceから日足データを取得"""
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": SYMBOL, "interval": "1d", "limit": limit}
    if start_ts:
        params["startTime"] = int(start_ts)
    res = requests.get(url, params=params)
    res.raise_for_status()
    return res.json()

def format_klines(raw_data):
    """APIの生データをDataFrameに変換"""
    df = pd.DataFrame(raw_data, columns=[
        'Open_time', 'Open', 'High', 'Low', 'Close', 'Volume',
        'Close_time', 'Quote_asset_volume', 'Number_of_trades',
        'Taker_buy_base', 'Taker_buy_quote', 'Ignore'
    ])
    df['Date'] = pd.to_datetime(df['Open_time'], unit='ms')
    df.set_index('Date', inplace=True)
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)
    return df

@st.cache_data(ttl=3600) # 1時間キャッシュ（API叩きすぎ防止）
def load_and_update_data():
    """CSVの読み込み、不足分の取得、またはゼロからの完全復旧"""
    if not os.path.exists(CSV_FILE):
        st.warning("⚠️ ローカルデータが見つかりません。2017年からの全データを取得して復旧します...")
        start_ts = 1502928000000 # Binance BTCUSDT開始時期 (2017-08)
        all_data = []
        while True:
            data = fetch_binance_klines(start_ts=start_ts, limit=1000)
            if not data: break
            all_data.extend(data)
            start_ts = data[-1][0] + 1 # 最後のデータの次のミリ秒
            if len(data) < 1000: break
        df = format_klines(all_data)
        df.to_csv(CSV_FILE)
        return df

    # CSVが存在する場合は差分更新
    df = pd.read_csv(CSV_FILE, index_col='Date', parse_dates=True)
    
    # 直近7日間を強制上書きするため、7日前のタイムスタンプを計算
    last_date = df.index[-1]
    update_start_date = last_date - pd.Timedelta(days=7)
    update_start_ts = int(update_start_date.timestamp() * 1000)
    
    # 差分取得
    new_data = fetch_binance_klines(start_ts=update_start_ts, limit=1000)
    df_new = format_klines(new_data)
    
    # マージ (古いデータを残しつつ、新しいデータで上書き・追加)
    df = df[df.index < df_new.index[0]] # 上書き部分を削除
    df = pd.concat([df, df_new])
    df.to_csv(CSV_FILE)
    return df

def get_realtime_price():
    """現在のリアルタイム価格を取得"""
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={SYMBOL}"
    res = requests.get(url)
    return float(res.json()['price'])

# ==========================================
# 3. 指標計算 & 戦略状態のシミュレーション
# ==========================================
def calculate_trigger_price(df, period_A, period_B):
    """MA(A)とMA(B)が交差する明日の価格Pを逆算"""
    sum_A_minus_1 = df['Close'].iloc[-(period_A - 1):].sum()
    sum_B_minus_1 = df['Close'].iloc[-(period_B - 1):].sum()
    if period_B == period_A: return 0
    return (period_A * sum_B_minus_1 - period_B * sum_A_minus_1) / (period_B - period_A)

# ==========================================
# 4. UI構築 (ダッシュボード)
# ==========================================
st.title("🚀 BTC Multi-Timeframe Strategy Dashboard")

# データ読み込み
with st.spinner("データを同期中..."):
    df_history = load_and_update_data()
    current_price = get_realtime_price()

# リアルタイムの未確定日足をデータフレームに追加
today = pd.Timestamp(datetime.now(timezone.utc).date())
if today in df_history.index:
    df_history.loc[today, 'Close'] = current_price
else:
    df_history.loc[today] = {'Open': current_price, 'High': current_price, 'Low': current_price, 'Close': current_price, 'Volume': 0}

# バックテストと完全一致する指標計算
ma_windows = [7, 30, 90, 365, 1460]
for w in ma_windows:
    df_history[f'MA_{w}'] = df_history['Close'].rolling(window=w).mean()

# 傾き(Slope)の計算 (当日 - 前日)
df_history['slope_7'] = df_history['MA_7'].diff()
df_history['slope_30'] = df_history['MA_30'].diff()

# --- バックテストロジックの完全再現による現在ステータスの算出 ---
current_base = 1.0  
is_short_mode_under_100 = False 

# UI表示用の最新フラグを保存する変数
latest_core_action = "現状維持"
latest_cond_perfect_bull = False
latest_cond_buy_gradual = False

for i in range(len(df_history)):
    c_close = df_history['Close'].iloc[i]
    c_7 = df_history['MA_7'].iloc[i]
    c_30 = df_history['MA_30'].iloc[i]
    c_90 = df_history['MA_90'].iloc[i]
    c_365 = df_history['MA_365'].iloc[i]
    c_1460 = df_history['MA_1460'].iloc[i]
    
    slope_7 = df_history['slope_7'].iloc[i]
    slope_30 = df_history['slope_30'].iloc[i]
    
    new_base = current_base
    target_long = 0.0
    target_short = 0.0
    
    if pd.notna(c_1460) and pd.notna(slope_30):
        # 1. 現物(Core)の判定
        cond_perfect_bull = (c_1460 < c_365 < c_90 < c_30 < c_7)
        cond_perfect_bear = (c_7 < c_30 < c_90 < c_365 < c_1460)
        cond_buy_gradual  = (c_7 < c_1460)
        cond_sell_gradual = (c_1460 < min(c_7, c_30)) and (max(c_7, c_30) < c_365) and (c_365 < c_90)
        
        buyback_flag = cond_perfect_bull or cond_buy_gradual
        sell_flag = cond_perfect_bear or cond_sell_gradual
        
        # 最終日のアクション記録用
        if i == len(df_history) - 1:
            latest_cond_perfect_bull = cond_perfect_bull
            latest_cond_buy_gradual = cond_buy_gradual
            if cond_perfect_bull: latest_core_action = "🟢 Perfect Bull (100%維持)"
            elif cond_perfect_bear: latest_core_action = "🔴 Perfect Bear (0%維持)"
            elif cond_buy_gradual: latest_core_action = "🟡 1% 買い集め進行中"
            elif cond_sell_gradual: latest_core_action = "🟠 3% 段階的売却中"
            else: latest_core_action = "⚪ 現状維持 (条件不一致)"

        if cond_perfect_bull:
            new_base = 1.0
        elif cond_perfect_bear:
            new_base = 0.0
        elif cond_buy_gradual:
            new_base = min(1.0, current_base + 0.01)
        elif cond_sell_gradual:
            new_base = max(0.0, current_base - 0.03)
            
        current_base = new_base
        
        # 2. オーバーレイ(Long/Short)の判定
        cap = (current_base / 2.0) + (1.0 - current_base)
        ideal_short = 0.0 
        
        if current_base == 1.0:
            is_short_mode_under_100 = False 
            short_cond_100 = (slope_30 < 0) and (slope_7 < 0) and ((slope_30 - slope_7) > 0) and (c_close < c_90)
            
            if short_cond_100:
                ideal_short = cap * 0.25
                target_long = 0.0
            else:
                ideal_short = 0.0
                target_long = cap * 1.0 
            
            # 現物100%時のショートは即時反映
            current_short = ideal_short
                
        else:
            if sell_flag:
                is_short_mode_under_100 = True
            if buyback_flag:
                is_short_mode_under_100 = False
            
            if is_short_mode_under_100:
                ideal_short = cap * 0.5
            else:
                ideal_short = 0.0
            
            target_long = 0.0
                
            # 現物<100%時のみ段階的にショートを移行
            if current_short < ideal_short:
                current_short = min(ideal_short, current_short + 0.015)
            elif current_short > ideal_short:
                current_short = max(ideal_short, current_short - 0.005)
            
# ループを抜け、現在（最新日）のポジションが確定
current_core_pct = current_base * 100
current_long_pct = target_long * 100
current_short_pct = current_short * 100 # ★target_short から current_short に変更

if current_long_pct > 0:
    overlay_status = f"🔵 LONG ({current_long_pct:.1f}%)"
elif current_short_pct > 0:
    overlay_status = f"🔴 SHORT ({current_short_pct:.1f}%)"
else:
    overlay_status = "⚪ ニュートラル (0%)"


# --- アラートモニター用のトリガー計算 (一番関与しやすい MA7 vs MA1460) ---
trigger_7_1460 = calculate_trigger_price(df_history.iloc[:-1], 7, 1460)
distance_to_trigger = (trigger_7_1460 - current_price) / current_price * 100

# --- 画面レイアウト ---
st.markdown("### 📊 現在の推奨アクション")
col1, col2, col3 = st.columns(3)
col1.metric("リアルタイム BTC価格", f"${current_price:,.2f}")
col2.metric(f"Core (現物): {current_core_pct:.0f}%", latest_core_action, delta_color="off")
col3.metric("Overlay (Long/Short)", overlay_status)

st.markdown("---")
st.subheader("⚠️ 24時間 アラート・モニター")
if latest_cond_perfect_bull:
     st.success("【安全】現在、強気のパーフェクトオーダーが成立しています。現物100%ホールド推奨です。")
elif latest_cond_buy_gradual:
    st.info(f"【進行中】MA(7) < MA(1460) の買い集めフェーズです。MA7がMA1460を上抜けるトリガー価格は ${trigger_7_1460:,.0f} (距離: {abs(distance_to_trigger):.1f}%) です。")
elif abs(distance_to_trigger) < 3.0:
    st.error(f"【警戒】現在価格がトリガー価格（${trigger_7_1460:,.0f}）まで {abs(distance_to_trigger):.1f}% に接近しています。買い集めフェーズへ移行する可能性があります。")
else:
    st.success(f"【待機】現在、主要なトリガーラインからの距離は十分あります。（距離: {abs(distance_to_trigger):.1f}%）")

# --- チャート描画 ---
st.subheader("📊 チャート")

# 1. 期間切り替えボタン（横並びのラジオボタン）の設置
period_options = {
    "1週間": 7,
    "1カ月": 30,
    "3カ月": 90,
    "1年": 365,
    "4年": 1460,
    "全期間": len(df_history)
}

selected_period = st.radio(
    "表示期間を選択:", 
    options=list(period_options.keys()), 
    index=4,          # デフォルトはインデックス4（「4年」）を選択
    horizontal=True   # 横並びに配置
)

# 2. 選択された日数に基づいてデータをスライス
display_days = period_options[selected_period]
df_plot = df_history.iloc[-display_days:] # ここで絞り込むことで、縦軸が自動で最適化されます

# 3. グラフの描画
fig = go.Figure()
fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['Close'], mode='lines', name='BTC Price', line=dict(color='black', width=1.5)))
fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MA_7'], mode='lines', name='MA(7)', line=dict(color='cyan', width=1)))
fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MA_30'], mode='lines', name='MA(30)', line=dict(color='blue', width=1)))
fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MA_90'], mode='lines', name='MA(90)', line=dict(color='green', width=1)))
fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MA_365'], mode='lines', name='MA(365)', line=dict(color='orange', width=1.5)))
fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MA_1460'], mode='lines', name='MA(1460)', line=dict(color='purple', width=2, dash='dot')))

# Y軸は対数表示(log)を維持したまま、表示範囲に応じて自動スケールされます[cite: 2]
fig.update_layout(
    yaxis_type="log", 
    height=380, 
    margin=dict(l=0, r=0, t=30, b=0), 
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)
st.plotly_chart(fig, use_container_width=True)

if st.button("🔄 リアルタイムデータを更新"):
    st.rerun()