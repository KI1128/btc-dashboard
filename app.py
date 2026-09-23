import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import matplotlib.pyplot as plt
import numpy as np
import datetime
import os
from data_manager import fetch_and_update_data, load_data, calculate_strategy, calculate_weather

st.set_page_config(page_title="BTC Dashboard", layout="wide")

st.markdown("""
    <style>
        .block-container { padding-top: 1rem; padding-bottom: 0rem; }
        [data-testid="stMetricValue"] { font-size: 1.8rem !important; }
        [data-testid="stMetricLabel"] { font-size: 0.9rem !important; }
    </style>
""", unsafe_allow_html=True)

@st.cache_data(ttl=3600)
def init_data():
    csv_path = "data/btc_daily_dataset.csv"
    needs_update = True
    if os.path.exists(csv_path):
        last_mod_time = datetime.datetime.fromtimestamp(os.path.getmtime(csv_path), datetime.timezone.utc)
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        start_of_utc_today = datetime.datetime.combine(now_utc.date(), datetime.time.min, tzinfo=datetime.timezone.utc)
        if last_mod_time >= start_of_utc_today:
            needs_update = False

    if needs_update:
        fetch_and_update_data(csv_path)
        
    df = load_data(csv_path)
    strat = calculate_strategy(df)
    weather = calculate_weather(df)
    return df, strat, weather

with st.spinner("🔄 最新の市場データを同期中..."):
    df, strat, weather = init_data()

st.title("🚀 BTC Dashboard")

# ================================
# タブ構成
# ================================
tab1, tab2, tab3 = st.tabs(["🏠 サマリー & お天気", "📈 ペイント対応チャート", "🧭 お天気4窓チャート"])

# --- タブ1: サマリー & お天気 ---
with tab1:
    # ---------------------------
    # 1段目: 現在の推奨戦略
    # ---------------------------
    st.header("🎯 1. 現在の推奨戦略")
    col1, col2 = st.columns(2)
    with col1:
        st.metric(f"Core (現物): {strat['core_pct']:.0f}%", strat['core_action'])
        if strat['long_pct'] > 0: overlay_status = f"🔵 LONG ({strat['long_pct']:.1f}%)"
        elif strat['short_pct'] > 0: overlay_status = f"🔴 SHORT ({strat['short_pct']:.1f}%)"
        else: overlay_status = "⚪ ニュートラル (0%)"
        st.metric("Overlay (Long/Short)", overlay_status)
    with col2:
        status_df = pd.DataFrame({
            "指標・条件": ["現在価格", "MA(7)", "MA(30)", "MA(90)", "MA(365)", "MA(1460)",
                       "🟢 超強気 (MA1460<365<90<30<7)", "🔴 超弱気 (MA7<30<90<365<1460)",
                       "🟡 買い集め (MA7 < MA1460)", "🟠 売却 (MA1460 < MA7/30 < 365 < 90)"],
            "数値・ステータス": [
                f"${strat['c_close']:,.2f}", f"${strat['c_7']:,.2f}", f"${strat['c_30']:,.2f}", 
                f"${strat['c_90']:,.2f}", f"${strat['c_365']:,.2f}", f"${strat['c_1460']:,.2f}",
                "成立" if strat['cond_perfect_bull'] else "不成立",
                "成立" if strat['cond_perfect_bear'] else "不成立",
                "成立" if strat['cond_buy_gradual'] else "不成立",
                "成立" if strat['cond_sell_gradual'] else "不成立"
            ]
        })
        st.table(status_df)
    
    st.divider()

    # ---------------------------
    # 2段目: 本日のお天気予報
    # ---------------------------
    st.header("🌤️ 2. 本日のお天気予報")
    
    # ボラティリティの判定（日次変動の標準偏差が3.5%以上なら警告表示）
    is_high_volatility = weather['sigma'] > 0.035
    if is_high_volatility:
        st.warning(f"⚠️ **【荒天注意】** 現在の市場はボラティリティが高くなっています（日次変動の標準偏差: {weather['sigma']*100:.1f}%）。荒れやすい相場展開にご注意ください。")
    
    col3, col4, col5 = st.columns(3)
    with col3:
        st.markdown(f"### トレンド: {weather['today_season']}")
    with col4:
        p90 = weather['today_past_90d_change']
        st.markdown(f"**直近90日の動き:** <br> {'☀️' if p90 >= 0 else '🌧️'} <span style='color:{'black' if p90 >= 0 else 'red'}; font-size: 1.5em;'>{p90:+.2f}%</span>", unsafe_allow_html=True)
    with col5:
        e90 = weather['today_exp_90d']
        st.markdown(f"**今後90日の見通し:** <br> {'↗️' if e90 >= 0 else '↘️'} <span style='color:{'black' if e90 >= 0 else 'red'}; font-size: 1.5em;'>{e90:+.2f}%</span>", unsafe_allow_html=True)

    st.divider()

    # ---------------------------
    # 3段目: 向こう1週間の天気
    # ---------------------------
    st.header("📆 3. 向こう1週間の天気")
    cols = st.columns(7)
    for i, col in enumerate(cols):
        res = weather['forecast_results'][i]
        with col:
            st.markdown(f"**{res['date'].strftime('%m/%d')}**")
            st.caption("トレンド")
            st.markdown(f"**{res['season']}**")
            f_p90 = res['past_90d']
            st.markdown(f"☀️ <span style='color:black'>+{f_p90:.1f}%</span>" if f_p90 >= 0 else f"🌧️ <span style='color:red'>{f_p90:.1f}%</span>", unsafe_allow_html=True)
            f_f90 = res['future_90d']
            st.markdown(f"↗️ <span style='color:black'>+{f_f90:.1f}%</span>" if f_f90 >= 0 else f"↘️ <span style='color:red'>{f_f90:.1f}%</span>", unsafe_allow_html=True)

# --- タブ2: ペイント対応チャート ---
with tab2:
    st.subheader("📈 チャート & 移動平均線ボード")
    
    col_ui1, col_ui2, col_ui3 = st.columns([2, 1, 1])
    with col_ui1:
        period_options = {"7日": 7, "30日": 30, "90日": 90, "1年": 365, "4年": 1460, "8年": 2920}
        selected_period = st.radio("表示期間を選択:", options=list(period_options.keys()), index=4, horizontal=True)
    with col_ui2:
        paint_color = st.color_picker("🎨 ペイントの色", "#FF0000")
    with col_ui3:
        paint_dash = st.selectbox("📏 線のスタイル", ["solid (実線)", "dash (破線)", "dot (点線)"])
        dash_val = paint_dash.split(" ")[0]

    display_days = period_options[selected_period]
    df_plot = df.iloc[-display_days:]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_plot['date'], y=df_plot['close'], mode='lines', name='BTC Price', line=dict(color='black', width=1.5)))
    fig.add_trace(go.Scatter(x=df_plot['date'], y=df_plot['MA_7'], mode='lines', name='MA(7)', line=dict(color='cyan', width=1)))
    fig.add_trace(go.Scatter(x=df_plot['date'], y=df_plot['MA_30'], mode='lines', name='MA(30)', line=dict(color='blue', width=1)))
    fig.add_trace(go.Scatter(x=df_plot['date'], y=df_plot['MA_90'], mode='lines', name='MA(90)', line=dict(color='green', width=1)))
    fig.add_trace(go.Scatter(x=df_plot['date'], y=df_plot['MA_365'], mode='lines', name='MA(365)', line=dict(color='orange', width=1.5)))
    fig.add_trace(go.Scatter(x=df_plot['date'], y=df_plot['MA_1460'], mode='lines', name='MA(1460)', line=dict(color='purple', width=2, dash='dot')))

    fig.update_layout(
        yaxis_type="log", height=600, 
        margin=dict(l=0, r=0, t=50, b=0), # 上部ツールバー用にマージンを追加
        # レジェンドをチャート下部中央に配置しツールチップとの被りを解消
        legend=dict(orientation="h", yanchor="top", y=-0.1, xanchor="center", x=0.5),
        dragmode='pan',
        newshape=dict(line_color=paint_color, line_dash=dash_val, line_width=2) 
    )
    
    paint_config = {
        'modeBarButtonsToAdd': ['drawline', 'drawhline', 'drawvline', 'eraseshape'],
        'displayModeBar': True,
        'editable': True
    }
    st.plotly_chart(fig, width='stretch', config=paint_config)

# --- タブ3: お天気4窓チャート ---
with tab3:
    st.subheader("🧭 マクロサイクル・エビデンスデータ (4窓)")
    
    # --- 表示オプションのトグル ---
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        show_trail = st.toggle("過去90日の軌跡を表示する", value=True)
    with col_t2:
        show_forecast = st.toggle("今後7日間の予想進路を表示する", value=True)
    
    plot_df = weather['valid_xy_df'].dropna(subset=['future_90d_return'])
    trail_df = weather['valid_xy_df'].iloc[-91:] # 過去90日分を取得
    
    mask_spring = (plot_df['MA_30'] > plot_df['MA_365']) & (plot_df['ma365_slope'] <= 0)
    mask_summer = (plot_df['MA_30'] > plot_df['MA_365']) & (plot_df['ma365_slope'] > 0)
    mask_autumn = (plot_df['MA_30'] <= plot_df['MA_365']) & (plot_df['ma365_slope'] > 0)
    mask_winter = (plot_df['MA_30'] <= plot_df['MA_365']) & (plot_df['ma365_slope'] <= 0)

    fig_4, axes = plt.subplots(2, 2, figsize=(14, 8), sharex=True, sharey=True)
    phases = [
        (mask_spring, axes[0, 0], 'Spring [Bottom Reversal]'), (mask_summer, axes[0, 1], 'Summer [Bull Market]'),
        (mask_autumn, axes[1, 0], 'Autumn [Peak Out]'), (mask_winter, axes[1, 1], 'Winter [Bear Market]')
    ]

    target_is_gc = weather['current_data']['MA_30'] > weather['current_data']['MA_365']
    target_is_up = weather['current_data']['ma365_slope'] > 0

    for mask, ax, title in phases:
        df_sub = plot_df[mask]
        ax.scatter(df_sub['macro_spread'], df_sub['past_90d_return'], 
                   c=df_sub['future_90d_return'], cmap='coolwarm_r', alpha=0.6, edgecolors='w', s=40, vmin=-60, vmax=80)
        ax.axvline(0, color='black', linestyle='--', alpha=0.6)
        ax.axhline(0, color='black', linestyle='--', alpha=0.6)
        ax.set_title(title, fontsize=12)
        
        # 現在いる季節の窓にだけ、軌跡・現在地・予想進路を描画
        if (target_is_gc == ('Spring' in title or 'Summer' in title)) and (target_is_up == ('Summer' in title or 'Autumn' in title)):
            
            # 1. 過去90日の軌跡
            if show_trail:
                sub_trail = trail_df[(trail_df['MA_30'] > trail_df['MA_365']) == target_is_gc]
                if len(sub_trail) > 1:
                    ax.scatter(sub_trail['macro_spread'], sub_trail['past_90d_return'], color='purple', s=12, alpha=0.6, zorder=4)
                    
            # 2. 今後7日間の予想進路
            if show_forecast:
                ax.plot(weather['x_forecast'], weather['y_forecast'], color='red', linestyle='--', linewidth=2, alpha=0.8, zorder=5)
                daily_vol_pct = weather['sigma'] * 100
                for i in range(1, 8):
                    ax.scatter(weather['x_forecast'][i], weather['y_forecast'][i], color='red', s=20, alpha=0.9, zorder=6)
                    radius = 5 + (8 * (daily_vol_pct * np.sqrt(i)))
                    ax.scatter(weather['x_forecast'][i], weather['y_forecast'][i], color='none', edgecolors='red', 
                               linewidth=1.5, linestyle=':', s=radius**2, alpha=0.6, zorder=6)
            
            # 3. 現在地（これは常に表示）
            ax.scatter(weather['current_data']['macro_spread'], weather['current_data']['past_90d_return'], 
                       color='gold', edgecolors='black', marker='*', s=150, zorder=7)

    for ax in axes[-1, :]: ax.set_xlabel('Macro Spread: (MA365 - MA1460) / MA1460 (%)')
    for ax in axes[:, 0]: ax.set_ylabel('Past 90-Day Return (%)')

    plt.tight_layout() 
    st.pyplot(fig_4)