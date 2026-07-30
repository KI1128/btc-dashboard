import streamlit as st
import pandas as pd
from streamlit_autorefresh import st_autorefresh
import numpy as np
import requests
import os
import time
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

st_autorefresh(interval=60000, limit=None, key="data_refresh")

CSV_FILE = 'btc_daily_dataset.csv'
SYMBOL = 'BTCUSDT'

# ==========================================
# 2. データ取得・更新ロジック (Binance API)
# ==========================================
def fetch_binance_klines(start_ts=None, limit=1000):
    url = "https://api.binance.us/api/v3/klines"
    params = {"symbol": SYMBOL, "interval": "1d", "limit": limit}
    if start_ts:
        params["startTime"] = int(start_ts)
    res = requests.get(url, params=params)
    res.raise_for_status()
    return res.json()

def format_klines(raw_data):
    df = pd.DataFrame(raw_data, columns=[
        'Open_time', 'Open', 'High', 'Low', 'Close', 'Volume',
        'Close_time', 'Quote_asset_volume', 'Number_of_trades',
        'Taker_buy_base', 'Taker_buy_quote', 'Ignore'
    ])
    df['Date'] = pd.to_datetime(df['Open_time'], unit='ms')
    df.set_index('Date', inplace=True)
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)
    return df

@st.cache_data(ttl=3600)
def load_and_update_data():
    if not os.path.exists(CSV_FILE):
        start_ts = 1502928000000 
        all_data = []
        while True:
            data = fetch_binance_klines(start_ts=start_ts, limit=1000)
            if not data: break
            all_data.extend(data)
            start_ts = data[-1][0] + 1
            if len(data) < 1000: break
            time.sleep(0.5)
        df = format_klines(all_data)
        df.to_csv(CSV_FILE)
        return df

    df = pd.read_csv(CSV_FILE, index_col='Date', parse_dates=True)
    last_date = df.index[-1]
    update_start_date = last_date - pd.Timedelta(days=7)
    update_start_ts = int(update_start_date.timestamp() * 1000)
    
    new_data = fetch_binance_klines(start_ts=update_start_ts, limit=1000)
    df_new = format_klines(new_data)
    
    df = df[df.index < df_new.index[0]]
    df = pd.concat([df, df_new])
    df.to_csv(CSV_FILE)
    return df

def get_realtime_price():
    url = f"https://api.binance.us/api/v3/ticker/price?symbol={SYMBOL}"
    res = requests.get(url)
    return float(res.json()['price'])

# ==========================================
# 3. 指標計算 & 戦略状態のシミュレーション
# ==========================================
def calculate_trigger_price(df, period_A, period_B):
    sum_A_minus_1 = df['Close'].iloc[-(period_A - 1):].sum()
    sum_B_minus_1 = df['Close'].iloc[-(period_B - 1):].sum()
    if period_B == period_A: return 0
    return (period_A * sum_B_minus_1 - period_B * sum_A_minus_1) / (period_B - period_A)

def calc_kpi(returns_series):
    cum_returns = (1 + returns_series).cumprod()
    if len(cum_returns) < 2:
        return 0, 0, 0
    days = (cum_returns.index[-1] - cum_returns.index[0]).days
    if days == 0:
        return 0, 0, 0
    
    cagr = (cum_returns.iloc[-1] ** (365.0 / days)) - 1
    rolling_max = cum_returns.cummax()
    drawdown = cum_returns / rolling_max - 1
    mdd = drawdown.min()
    calmar = cagr / abs(mdd) if mdd != 0 else 0
    
    return cagr * 100, mdd * 100, calmar

# ==========================================
# 4. データ準備・バックテスト並行処理
# ==========================================
with st.spinner("データを同期中..."):
    df_history = load_and_update_data()
    current_price = get_realtime_price()

today = pd.Timestamp(datetime.now(timezone.utc).date())
if today in df_history.index:
    df_history.loc[today, 'Close'] = current_price
else:
    df_history.loc[today] = {'Open': current_price, 'High': current_price, 'Low': current_price, 'Close': current_price, 'Volume': 0}

ma_windows = [7, 30, 90, 365, 1460]
for w in ma_windows:
    df_history[f'MA_{w}'] = df_history['Close'].rolling(window=w).mean()

df_history['slope_7'] = df_history['MA_7'].diff()
df_history['slope_30'] = df_history['MA_30'].diff()

current_base = 1.0  
current_short = 0.0
is_short_mode_under_100 = False
short_hold_days = 0 
long_active = False
days_since_long_exit = 999
long_penalty = False

# バックテスト用の履歴リスト
core_weights = []
long_weights = []
short_weights = []

# 現在のステータス保存用変数
latest_cond_perfect_bull = False
latest_cond_perfect_bear = False
latest_cond_buy_gradual = False
latest_cond_sell_gradual = False
latest_short_cond_100 = False
latest_c_close = latest_c_7 = latest_c_30 = latest_c_90 = latest_c_365 = latest_c_1460 = 0.0

# --- オーバーレイの詳細ステータス保存用 ---
latest_long_penalty = False
latest_days_since_long_exit = 0
latest_short_hold_days = 0
latest_long_signal = False

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
    
    if pd.notna(c_1460) and pd.notna(slope_30):
        cond_perfect_bull = (c_1460 < c_365 < c_90 < c_30 < c_7)
        cond_perfect_bear = (c_7 < c_30 < c_90 < c_365 < c_1460)
        cond_buy_gradual  = (c_7 < c_1460)
        cond_sell_gradual = (c_1460 < min(c_7, c_30)) and (max(c_7, c_30) < c_365) and (c_365 < c_90)
        
        buyback_flag = cond_perfect_bull or cond_buy_gradual
        sell_flag = cond_perfect_bear or cond_sell_gradual
        
        # 最終日のアクションと各指標の記録
        if i == len(df_history) - 1:
            latest_c_close, latest_c_7, latest_c_30, latest_c_90, latest_c_365, latest_c_1460 = c_close, c_7, c_30, c_90, c_365, c_1460
            latest_cond_perfect_bull = cond_perfect_bull
            latest_cond_perfect_bear = cond_perfect_bear
            latest_cond_buy_gradual = cond_buy_gradual
            latest_cond_sell_gradual = cond_sell_gradual
            if cond_perfect_bull: latest_core_action = "🟢 Perfect Bull (100%維持)"
            elif cond_perfect_bear: latest_core_action = "🔴 Perfect Bear (0%維持)"
            elif cond_buy_gradual: latest_core_action = "🟡 1% 買い集め進行中"
            elif cond_sell_gradual: latest_core_action = "🟠 3% 段階的売却中"
            else: latest_core_action = "⚪ 現状維持 (条件不一致)"

        if cond_perfect_bull: new_base = 1.0
        elif cond_perfect_bear: new_base = 0.0
        elif cond_buy_gradual: new_base = min(1.0, current_base + 0.01)
        elif cond_sell_gradual: new_base = max(0.0, current_base - 0.03)
            
        current_base = new_base
        
        cap = (current_base / 2.0) + (1.0 - current_base)
        
        if current_base == 1.0:
            is_short_mode_under_100 = False 
            short_cond_100 = (slope_30 < 0) and (slope_7 < 0) and ((slope_30 - slope_7) > 0) and (c_close < c_90)
            if i == len(df_history) - 1: latest_short_cond_100 = short_cond_100
            
            # --- ショートの7日間強制解除 ---
            if short_cond_100 and short_hold_days < 7:
                ideal_short = cap * 0.25
                short_hold_days += 1
            else:
                ideal_short = 0.0
                # ショート条件自体が否定された場合のみ日数をリセット
                if not short_cond_100:
                    short_hold_days = 0
            
            current_short = ideal_short

            # --- ロングのペナルティ（お預け）判定と特例 ---
            if ideal_short > 0.0:
                long_penalty = False

            strong_bull = (c_7 > c_30) and (c_30 > c_90)
            if strong_bull:
                long_penalty = False 

            long_signal = (ideal_short == 0.0) and (c_7 > c_30)
            is_long_active_today = False

            if long_signal:
                if not long_active:
                    if (days_since_long_exit <= 7) and not strong_bull:
                        long_penalty = True
                
                if not long_penalty:
                    target_long = cap * 1.0
                    is_long_active_today = True
                else:
                    target_long = 0.0
            else:
                target_long = 0.0
            
            # 状態の更新
            if long_active and not is_long_active_today:
                days_since_long_exit = 0  
            elif not is_long_active_today:
                days_since_long_exit += 1 
                
            long_active = is_long_active_today

        else:
            # 現物100%状態を抜けたら念のため各タイマーとフラグをリセット
            short_hold_days = 0 
            long_active = False
            days_since_long_exit = 999
            long_penalty = False
            
            if i == len(df_history) - 1: latest_short_cond_100 = False
            if sell_flag: is_short_mode_under_100 = True
            if buyback_flag: is_short_mode_under_100 = False
            
            ideal_short = cap * 0.5 if is_short_mode_under_100 else 0.0
            target_long = 0.0
                
            if current_short < ideal_short: current_short = min(ideal_short, current_short + 0.015)
            elif current_short > ideal_short: current_short = max(ideal_short, current_short - 0.005)
            
    core_weights.append(current_base)
    long_weights.append(target_long)
    short_weights.append(current_short)
    
    # 最終日の詳細ステータスをUI向けに保存 ---
    if i == len(df_history) - 1:
        latest_long_penalty = long_penalty
        latest_days_since_long_exit = days_since_long_exit
        latest_short_hold_days = short_hold_days
        latest_long_signal = (ideal_short == 0.0) and (c_7 > c_30) if current_base == 1.0 else False

# バックテストリターン計算
df_history['Core_Weight'] = core_weights
df_history['Long_Weight'] = long_weights
df_history['Short_Weight'] = short_weights

df_history['Core_Weight_Prev'] = df_history['Core_Weight'].shift(1).fillna(1.0)
df_history['Long_Weight_Prev'] = df_history['Long_Weight'].shift(1).fillna(0.0)
df_history['Short_Weight_Prev'] = df_history['Short_Weight'].shift(1).fillna(0.0)

df_history['BTC_Return'] = df_history['Close'].pct_change().fillna(0)
df_history['Core_Return'] = df_history['Core_Weight_Prev'] * df_history['BTC_Return']
df_history['Long_Return'] = df_history['Long_Weight_Prev'] * df_history['BTC_Return']
df_history['Short_Return'] = df_history['Short_Weight_Prev'] * -df_history['BTC_Return']
df_history['Total_Return'] = df_history['Core_Return'] + df_history['Long_Return'] + df_history['Short_Return']

# 状態の確定
current_core_pct = current_base * 100
current_long_pct = target_long * 100
current_short_pct = current_short * 100

if current_long_pct > 0: overlay_status = f"🔵 LONG ({current_long_pct:.1f}%)"
elif current_short_pct > 0: overlay_status = f"🔴 SHORT ({current_short_pct:.1f}%)"
else: overlay_status = "⚪ ニュートラル (0%)"

# ==========================================
# 5. アラート用トリガー距離の算出
# ==========================================
# --- ベース（現物）用トリガー ---
trigger_7_1460 = calculate_trigger_price(df_history.iloc[:-1], 7, 1460)
dist_7_1460 = (trigger_7_1460 - current_price) / current_price * 100

trigger_7_30 = calculate_trigger_price(df_history.iloc[:-1], 7, 30)
dist_7_30 = (trigger_7_30 - current_price) / current_price * 100

# --- オーバーレイ（Long/Short）用トリガー ---
if len(df_history) >= 31:
    p_7_ago = df_history['Close'].iloc[-8]
    p_30_ago = df_history['Close'].iloc[-31]
    
    # ショート条件を分かつ厳密な境界価格
    trigger_slope7 = p_7_ago
    trigger_slope30 = p_30_ago
    trigger_slope_diff = (30 * p_7_ago - 7 * p_30_ago) / 23
    trigger_price_90 = df_history['MA_90'].iloc[-2] if len(df_history) > 1 else current_price
else:
    trigger_slope7 = trigger_slope30 = trigger_slope_diff = trigger_price_90 = current_price

# 各条件が「成立」または「バッファ圏内（危険水域）」にあるかの判定
# ロング時：下落してトリガーに接近（またはすでに下抜け）でTrue
warn_slope7_down = (current_price <= trigger_slope7 * 1.01)
warn_slope30_down = (current_price <= trigger_slope30 * 1.01)
warn_diff_down = (current_price <= trigger_slope_diff * 1.01)
warn_ma90_down = (current_price <= trigger_price_90 * 1.05)

# ショート時：上昇してトリガーに接近（またはすでに上抜け）でTrue
warn_slope7_up = (current_price >= trigger_slope7 * 0.99)
warn_slope30_up = (current_price >= trigger_slope30 * 0.99)
warn_diff_up = (current_price >= trigger_slope_diff * 0.99)
warn_ma90_up = (current_price >= trigger_price_90 * 0.95)

# 複合警戒フラグ
short_initiation_danger = warn_slope7_down and warn_slope30_down and warn_diff_down and warn_ma90_down
short_dissolution_danger = warn_slope7_up or warn_slope30_up or warn_diff_up or warn_ma90_up


# ==========================================
# 6. UI構築 (タブ分け)
# ==========================================
st.title("🚀 BTC Strategy Dashboard")

tab1, tab2, tab3 = st.tabs(["📊 ダッシュボード", "📈 チャート", "🧪 戦術バックテスト"])

# --- タブ1: ダッシュボード ---
with tab1:
    st.markdown("### 現在の推奨アクション")
    
    prev_price = df_history['Close'].iloc[-2] if len(df_history) > 1 else current_price
    price_diff = current_price - prev_price
    price_pct = (price_diff / prev_price) * 100 if prev_price != 0 else 0

    col1, col2, col3 = st.columns(3)
    col1.metric("リアルタイム BTC価格", f"${current_price:,.2f}", f"{price_diff:+,.2f} ({price_pct:+.2f}%)")
    col2.metric(f"Core (現物): {current_core_pct:.0f}%", latest_core_action, delta_color="off")
    col3.metric("Overlay (Long/Short)", overlay_status)

    st.markdown("---")
    st.subheader("⚠️ 24時間 アラート・モニター")
    
    # -----------------------------------
    # 1. ベース（現物）戦略のアラート
    # -----------------------------------
    st.markdown("##### 📦 Core (現物) ポジション")
    if latest_cond_perfect_bull:
        st.success("✅【安全】現在、強気のパーフェクトオーダーが成立しています。現物100%ホールド推奨です。")
        if abs(dist_7_30) <= 5.0:
            st.warning(f"⚠️【警戒】MA7がMA30をデッドクロスする価格（${trigger_7_30:,.0f} / 残り {abs(dist_7_30):.1f}%）に接近しています。5%以内のためパーフェクトオーダー崩壊に警戒してください。")
        else:
            st.info(f"ℹ️ パーフェクトオーダー崩壊ライン（MA7 vs MA30）は ${trigger_7_30:,.0f}（距離: {abs(dist_7_30):.1f}%）です。")
            
    elif latest_cond_perfect_bear:
        st.error("🚨【危険】現在、弱気のパーフェクトオーダーが成立しています。現物0%（全キャッシュ化）推奨です。")
        if abs(dist_7_30) <= 5.0:
             st.warning(f"⚠️【警戒】下落トレンド脱出ライン（MA7 vs MA30）まで残り {abs(dist_7_30):.1f}% です。")
        else:
             st.info(f"ℹ️ 下落トレンド脱出ライン（MA7 vs MA30）は ${trigger_7_30:,.0f}（距離: {abs(dist_7_30):.1f}%）です。")
             
    elif latest_cond_buy_gradual:
        st.info(f"ℹ️【買い集め】MA(7) < MA(1460) が成立中。毎日1%ずつ現物を買い集めるフェーズです。")
        if abs(dist_7_1460) <= 5.0:
            st.warning(f"⚠️【接近中】MA7がMA1460を上抜ける大局的な転換価格（${trigger_7_1460:,.0f} / 残り {abs(dist_7_1460):.1f}%）に接近しています。5%以内のため注視してください。")
        else:
            st.info(f"ℹ️ 買い集めフェーズ終了ライン（MA7 vs MA1460）は ${trigger_7_1460:,.0f}（距離: {abs(dist_7_1460):.1f}%）です。")
            
    elif latest_cond_sell_gradual:
        st.warning("⚠️【段階的売却】下落トレンドの初期症状を検知しました。毎日3%ずつ現物を段階的に売却するフェーズです。")
        if abs(dist_7_30) <= 5.0:
             st.warning(f"⚠️【警戒】トレンド好転の目安となるライン（MA7 vs MA30）まで残り {abs(dist_7_30):.1f}% です。")
        else:
             st.info(f"ℹ️ トレンド好転の目安となるライン（MA7 vs MA30）は ${trigger_7_30:,.0f}（距離: {abs(dist_7_30):.1f}%）です。")
    else:
        st.success("✅【待機】現在、条件移行の主要トリガーラインからの距離は十分にあります。現状維持です。")

    # -----------------------------------
    # 2. オーバーレイ（Long/Short）戦略のアラート
    # -----------------------------------
    st.markdown("##### 🚀 Overlay (Long/Short) ポジション")
    if current_base == 1.0:
        if current_short > 0:
            if latest_short_cond_100:
                st.warning(f"📉【ヘッジ発動】現物は100%ですが、短期的な下落サインを検知しました。ショートヘッジを展開中です。（強制保持: {latest_short_hold_days}/7日）")
            else:
                st.warning(f"📉【ヘッジ維持】下落サインは消滅しましたが、7日間強制保持ルールによりショートを維持しています。（強制保持: {latest_short_hold_days}/7日）")
                
            if short_dissolution_danger:
                st.warning(f"⚠️【警戒】ショート解除条件のいずれかが境界価格から1%(MA90は5%)以内に接近しています。トレンドが好転し、再度ロングへ転換する可能性があります。")
            else:
                st.info("ℹ️ ショート解除条件（4つのうちいずれかの上抜け）からは十分な距離があります。")
        else:
            if current_long_pct > 0:
                st.success("🟢【ロング】上昇トレンド継続中です。ショートを解除し、ロング（追撃）ポジションを展開しています。")
                if short_initiation_danger:
                    st.warning(f"⚠️【警戒】すべてのショート発動条件（MAの傾き悪化＋MA90割れ）が境界価格から1%(MA90は5%)以内に迫っています。急落によるロング強制解除に強く警戒してください。")
                else:
                    st.info("ℹ️ ショート発動条件のすべてが同時に満たされるまでには、まだ十分な距離があります。")
            else:
                if latest_long_signal and latest_long_penalty:
                    st.info(f"⏳【ロング待機 (ペナルティ)】ロング条件は成立していますが、ダマシ回避のため解除後7日間のペナルティ期間中です。エントリーを保留しています。（経過: {latest_days_since_long_exit}日）")
                else:
                    st.info("⚪【ニュートラル】ショート条件は非成立ですが、ロング条件(MA7 > MA30)も満たしていないため、オーバーレイは現在フラット（ポジションなし）です。")
    else:
        if is_short_mode_under_100:
            st.warning("📉【ショート展開】現物ポジション縮小に伴い、下落ヘッジのためのショートポジションを構築・維持しています。")
            if abs(dist_7_1460) <= 5.0:
                st.warning(f"⚠️【警戒】買い戻し条件（MA7がMA1460を上抜ける）まで残り {abs(dist_7_1460):.1f}% です。ショートが解消される可能性があります。")
        else:
            st.info("🔄【ショート償却】買い戻し条件が成立したため、ショートポジションを徐々に縮小・償却しています。")

    st.markdown("#### 🔍 各指標・条件判定の一覧")
    status_df = pd.DataFrame({
        "指標・条件": [
            "現在価格", "MA(7)", "MA(30)", "MA(90)", "MA(365)", "MA(1460)",
            "超強気フェーズ (MA1460<365<90<30<7)",
            "超弱気フェーズ (MA7<30<90<365<1460)",
            "買い集めフェーズ (MA7 < MA1460)",
            "売却・ロング禁止フェーズ (MA1460 < MA7/30 < 365 < 90)",
            "ショート移行条件成立（現物保有時ロング解消条件成立）"
        ],
        "数値・ステータス": [
            f"${latest_c_close:,.2f}", f"${latest_c_7:,.2f}", f"${latest_c_30:,.2f}", 
            f"${latest_c_90:,.2f}", f"${latest_c_365:,.2f}", f"${latest_c_1460:,.2f}",
            "🟢 成立" if latest_cond_perfect_bull else "❌ 不成立",
            "🔴 成立" if latest_cond_perfect_bear else "❌ 不成立",
            "🟡 成立" if latest_cond_buy_gradual else "❌ 不成立",
            "🟠 成立" if latest_cond_sell_gradual else "❌ 不成立",
            "🔴 成立" if is_short_mode_under_100 or latest_short_cond_100 else "❌ 不成立"
        ]
    })
    st.table(status_df)
    
    if st.button("🔄 リアルタイムデータを更新"):
        st.rerun()

# --- タブ2: チャート ---
with tab2:
    st.subheader("相場環境チャート")
    period_options = {
        "1週間": 7,
        "1カ月": 30,
        "3カ月": 90,
        "1年": 365,
        "4年": 1460
    }

    selected_period = st.radio("表示期間を選択:", options=list(period_options.keys()), index=4, horizontal=True)

    display_days = period_options[selected_period]
    df_plot = df_history.iloc[-display_days:]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['Close'], mode='lines', name='BTC Price', line=dict(color='black', width=1.5)))
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MA_7'], mode='lines', name='MA(7)', line=dict(color='cyan', width=1)))
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MA_30'], mode='lines', name='MA(30)', line=dict(color='blue', width=1)))
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MA_90'], mode='lines', name='MA(90)', line=dict(color='green', width=1)))
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MA_365'], mode='lines', name='MA(365)', line=dict(color='orange', width=1.5)))
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MA_1460'], mode='lines', name='MA(1460)', line=dict(color='purple', width=2, dash='dot')))

    fig.update_layout(
        yaxis_type="log", 
        height=500, 
        margin=dict(l=0, r=0, t=30, b=0), 
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig, use_container_width=True)

# --- タブ3: 戦術バックテスト ---
with tab3:
    st.subheader("戦略の有効性検証")
    
    # 評価期間を直近の4年間 (1460日) に設定
    bt_start = df_history.index[-1] - pd.Timedelta(days=1460)
    df_bt = df_history.loc[bt_start:].copy()
    
    # KPIの計算
    hodl_cagr, hodl_mdd, hodl_cal = calc_kpi(df_bt['BTC_Return'])
    core_cagr, core_mdd, core_cal = calc_kpi(df_bt['Core_Return'])
    long_cagr, long_mdd, long_cal = calc_kpi(df_bt['Long_Return'])
    short_cagr, short_mdd, short_cal = calc_kpi(df_bt['Short_Return'])
    total_cagr, total_mdd, total_cal = calc_kpi(df_bt['Total_Return'])
    
    start_str = df_bt.index[0].strftime("%Y-%m-%d")
    end_str = df_bt.index[-1].strftime("%Y-%m-%d")
    
    st.markdown(f"**評価期間:** {start_str} 〜 {end_str}")
    
    # KPIテーブル
    kpi_data = {
        "戦略": ["HODL (参考)", "Core (現物)", "Long (追撃)", "Short (空売)", "総合戦略"],
        "CAGR (%)": [f"{hodl_cagr:.2f}%", f"{core_cagr:.2f}%", f"{long_cagr:.2f}%", f"{short_cagr:.2f}%", f"{total_cagr:.2f}%"],
        "MDD (%)": [f"{hodl_mdd:.2f}%", f"{core_mdd:.2f}%", f"{long_mdd:.2f}%", f"{short_mdd:.2f}%", f"{total_mdd:.2f}%"],
        "カルマーレシオ": [f"{hodl_cal:.2f}", f"{core_cal:.2f}", f"{long_cal:.2f}", f"{short_cal:.2f}", f"{total_cal:.2f}"]
    }
    st.table(pd.DataFrame(kpi_data).set_index("戦略"))
    
    # 各戦略の累積資産推移を計算
    df_bt['HODL_Cum'] = (1 + df_bt['BTC_Return']).cumprod()
    df_bt['Core_Cum'] = (1 + df_bt['Core_Return']).cumprod()
    df_bt['Long_Cum'] = (1 + df_bt['Long_Return']).cumprod()
    df_bt['Short_Cum'] = (1 + df_bt['Short_Return']).cumprod()
    df_bt['Total_Cum'] = (1 + df_bt['Total_Return']).cumprod()
    
    # グラフへの追加 (5本の線)
    fig_bt = go.Figure()
    fig_bt.add_trace(go.Scatter(x=df_bt.index, y=df_bt['HODL_Cum'], mode='lines', name='HODL (BTC)', line=dict(color='gray', dash='dash')))
    fig_bt.add_trace(go.Scatter(x=df_bt.index, y=df_bt['Core_Cum'], mode='lines', name='Core (現物)', line=dict(color='orange', width=1)))
    fig_bt.add_trace(go.Scatter(x=df_bt.index, y=df_bt['Long_Cum'], mode='lines', name='Long (追撃)', line=dict(color='cyan', width=1)))
    fig_bt.add_trace(go.Scatter(x=df_bt.index, y=df_bt['Short_Cum'], mode='lines', name='Short (空売)', line=dict(color='purple', width=1)))
    fig_bt.add_trace(go.Scatter(x=df_bt.index, y=df_bt['Total_Cum'], mode='lines', name='総合戦略', line=dict(color='red', width=2.5)))
    
    fig_bt.update_layout(
        title="資産推移比較 (初期=1.0)",
        yaxis_type="log",
        height=450,
        margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig_bt, use_container_width=True)