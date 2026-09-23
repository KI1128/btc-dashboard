import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.subplots as sp
import numpy as np
import datetime
import os
from data_manager import fetch_and_update_data, load_data, calculate_strategy, calculate_weather

st.set_page_config(page_title="統合版 BTC Dashboard", layout="wide")

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

st.title("🚀 BTC 統合ダッシュボード")

tab1, tab2, tab3 = st.tabs(["🏠 サマリー & お天気", "📈 ペイント対応チャート", "🧭 お天気エビデンスデータ"])

# --- タブ1: サマリー & お天気 ---
with tab1:
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

    st.header("🌤️ 2. 本日のお天気予報")
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
    
    col_ui1, col_ui2, col_ui3, col_ui4 = st.columns([2, 1, 1, 1.5])
    with col_ui1:
        period_options = {"7日": 7, "30日": 30, "90日": 90, "1年": 365, "4年": 1460, "8年": 2920}
        selected_period = st.radio("表示期間を選択:", options=list(period_options.keys()), index=4, horizontal=True)
    with col_ui2:
        paint_color = st.color_picker("🎨 ペイントの色", "#FF0000")
    with col_ui3:
        paint_dash = st.selectbox("📏 線のスタイル", ["solid (実線)", "dash (破線)", "dot (点線)"])
        dash_val = paint_dash.split(" ")[0]
    with col_ui4:
        st.markdown("<div style='margin-top: 30px;'></div>", unsafe_allow_html=True)
        show_price_cloud = st.toggle("☁️ 90日先の予想パス（雲）と統計ラインを表示", value=False)

    display_days = period_options[selected_period]
    df_plot = df.iloc[-display_days:]

    fig = go.Figure()
    
    # 🆕 価格チャートの雲（メガホン状）と統計ラインを描画
    if show_price_cloud:
        cloud_x_concat = []
        cloud_y_concat = []
        base_date = df_plot['date'].iloc[-1]
        base_price = df_plot['close'].iloc[-1]
        
        # 1. 雲 (300パス) の描画
        for sim_path in weather['sim_prices_90d']:
            cloud_x_concat.extend([base_date] + weather['future_dates_90d'] + [None])
            cloud_y_concat.extend([base_price] + sim_path + [None])
            
        fig.add_trace(go.Scatter(
            x=cloud_x_concat, y=cloud_y_concat, mode='lines',
            line=dict(color='rgba(255, 0, 0, 0.02)', width=1), 
            hoverinfo='skip', showlegend=False
        ))

        # 2. 統計ラインの描画
        dates_with_base = [base_date] + weather['future_dates_90d']
        
        fig.add_trace(go.Scatter(
            x=dates_with_base, y=[base_price] + weather['mean_path_90d'],
            mode='lines', line=dict(color='red', width=2, dash='dash'), name='Mean (平均)'
        ))
        fig.add_trace(go.Scatter(
            x=dates_with_base, y=[base_price] + weather['upper_1sd_90d'],
            mode='lines', line=dict(color='rgba(255, 0, 0, 0.4)', width=1, dash='dash'), name='+1 SD (標準偏差)'
        ))
        fig.add_trace(go.Scatter(
            x=dates_with_base, y=[base_price] + weather['lower_1sd_90d'],
            mode='lines', line=dict(color='rgba(255, 0, 0, 0.4)', width=1, dash='dash'), name='-1 SD (標準偏差)'
        ))

    # メインの価格線とMA
    fig.add_trace(go.Scatter(x=df_plot['date'], y=df_plot['close'], mode='lines', name='BTC Price', line=dict(color='black', width=1.5)))
    fig.add_trace(go.Scatter(x=df_plot['date'], y=df_plot['MA_7'], mode='lines', name='MA(7)', line=dict(color='cyan', width=1)))
    fig.add_trace(go.Scatter(x=df_plot['date'], y=df_plot['MA_30'], mode='lines', name='MA(30)', line=dict(color='blue', width=1)))
    fig.add_trace(go.Scatter(x=df_plot['date'], y=df_plot['MA_90'], mode='lines', name='MA(90)', line=dict(color='green', width=1)))
    fig.add_trace(go.Scatter(x=df_plot['date'], y=df_plot['MA_365'], mode='lines', name='MA(365)', line=dict(color='orange', width=1.5)))
    fig.add_trace(go.Scatter(x=df_plot['date'], y=df_plot['MA_1460'], mode='lines', name='MA(1460)', line=dict(color='purple', width=2, dash='dot')))

    fig.update_layout(
        yaxis_type="log", height=600, 
        margin=dict(l=0, r=0, t=50, b=0),
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

# --- タブ3: お天気エビデンスデータ ---
with tab3:
    st.subheader("🧭 マクロサイクル・エビデンスデータ")
    
    col_t1, col_t2 = st.columns([2, 3])
    with col_t1:
        view_mode = st.radio("🔍 表示モード:", ["4窓すべて", "Spring", "Summer", "Autumn", "Winter"], horizontal=True)
    with col_t2:
        col_t2_1, col_t2_2 = st.columns(2)
        with col_t2_1:
            show_trail = st.toggle("過去90日の軌跡を表示", value=True)
            show_forecast = st.toggle("今後90日間の予想（雲）を表示", value=True) # 表記変更
        with col_t2_2:
            if show_forecast:
                # 🆕 90日分(点数13倍)になったため、デフォルトを0.010に調整
                cloud_opacity = st.slider("☁️ 雲の濃さ調整", min_value=0.001, max_value=0.050, value=0.010, step=0.001, format="%.3f")
            else:
                cloud_opacity = 0.010
    
    plot_df = weather['valid_xy_df'].dropna(subset=['future_90d_return'])
    trail_df = weather['valid_xy_df'].iloc[-91:] 
    
    mask_spring = (plot_df['MA_30'] > plot_df['MA_365']) & (plot_df['ma365_slope'] <= 0)
    mask_summer = (plot_df['MA_30'] > plot_df['MA_365']) & (plot_df['ma365_slope'] > 0)
    mask_autumn = (plot_df['MA_30'] <= plot_df['MA_365']) & (plot_df['ma365_slope'] > 0)
    mask_winter = (plot_df['MA_30'] <= plot_df['MA_365']) & (plot_df['ma365_slope'] <= 0)

    phases = [
        (mask_spring, 1, 1, 'Spring'),
        (mask_summer, 1, 2, 'Summer'),
        (mask_autumn, 2, 1, 'Autumn'),
        (mask_winter, 2, 2, 'Winter')
    ]

    target_is_gc = weather['current_data']['MA_30'] > weather['current_data']['MA_365']
    target_is_up = weather['current_data']['ma365_slope'] > 0

    is_subplot = (view_mode == "4窓すべて")

    if is_subplot:
        fig_4 = sp.make_subplots(
            rows=2, cols=2,
            subplot_titles=('Spring [Bottom Reversal]', 'Summer [Bull Market]',
                            'Autumn [Peak Out]', 'Winter [Bear Market]'),
            horizontal_spacing=0.06, vertical_spacing=0.10
        )
        active_phases = phases
    else:
        fig_4 = go.Figure()
        active_phases = [p for p in phases if p[3] in view_mode]

    def add_t(trace, r, c):
        if is_subplot: fig_4.add_trace(trace, row=r, col=c)
        else: fig_4.add_trace(trace)

    for mask, row, col, season_name in active_phases:
        df_sub = plot_df[mask]
        
        add_t(
            go.Scatter(
                x=df_sub['macro_spread'], y=df_sub['past_90d_return'],
                mode='markers',
                marker=dict(
                    color=df_sub['future_90d_return'],
                    colorscale='RdBu', reversescale=False, cmin=-60, cmax=80,
                    size=7, opacity=0.6, line=dict(width=0.5, color='white')
                ),
                text=df_sub['date'].astype(str),
                hovertemplate="<b>%{text}</b><br>Macro Spread: %{x:.2f}%<br>Past 90D: %{y:.2f}%<br>Future 90D: %{marker.color:.2f}%<extra></extra>",
                showlegend=False
            ),
            row, col
        )
        
        if is_subplot:
            fig_4.add_hline(y=0, line_dash="dash", line_color="black", opacity=0.5, row=row, col=col)
            fig_4.add_vline(x=0, line_dash="dash", line_color="black", opacity=0.5, row=row, col=col)
        else:
            fig_4.add_hline(y=0, line_dash="dash", line_color="black", opacity=0.5)
            fig_4.add_vline(x=0, line_dash="dash", line_color="black", opacity=0.5)

        if (target_is_gc == (season_name in ['Spring', 'Summer'])) and (target_is_up == (season_name in ['Summer', 'Autumn'])):
            
            if show_trail:
                sub_trail = trail_df[(trail_df['MA_30'] > trail_df['MA_365']) == target_is_gc]
                if len(sub_trail) > 1:
                    add_t(
                        go.Scatter(
                            x=sub_trail['macro_spread'], y=sub_trail['past_90d_return'],
                            mode='markers',
                            marker=dict(color='purple', size=6, opacity=0.8),
                            text=sub_trail['date'].astype(str),
                            hovertemplate="<b>%{text}</b> (軌跡)<br>Spread: %{x:.2f}%<br>Past 90D: %{y:.2f}%<extra></extra>",
                            showlegend=False
                        ),
                        row, col
                    )
            
            if show_forecast:
                # 90日分の雲
                add_t(
                    go.Scatter(
                        x=weather['cloud_x'], y=weather['cloud_y'],
                        mode='markers',
                        marker=dict(color='red', size=8, opacity=cloud_opacity, line=dict(width=0)),
                        hoverinfo='skip', showlegend=False
                    ),
                    row, col
                )
                
                # 7日分の平均パス
                add_t(
                    go.Scatter(
                        x=weather['x_forecast'], y=weather['y_forecast'],
                        mode='lines', line=dict(color='red', width=2, dash='dash'),
                        hoverinfo='skip', showlegend=False
                    ),
                    row, col
                )
                
                # 7日分の各ノード
                for i in range(1, 8):
                    add_t(
                        go.Scatter(
                            x=[weather['x_forecast'][i]], y=[weather['y_forecast'][i]],
                            mode='markers',
                            marker=dict(color='red', size=7, line=dict(color='white', width=1)),
                            text=[f"Day +{i}"],
                            hovertemplate="<b>予想平均 %{text}</b><br>Spread: %{x:.2f}%<br>Past 90D: %{y:.2f}%<extra></extra>",
                            showlegend=False
                        ),
                        row, col
                    )

            add_t(
                go.Scatter(
                    x=[weather['current_data']['macro_spread']], y=[weather['current_data']['past_90d_return']],
                    mode='markers',
                    marker=dict(color='gold', size=16, symbol='star', line=dict(color='black', width=1)),
                    text=["現在地 (Today)"],
                    hovertemplate="<b>%{text}</b><br>Spread: %{x:.2f}%<br>Past 90D: %{y:.2f}%<extra></extra>",
                    showlegend=False
                ),
                row, col
            )

    if is_subplot:
        fig_4.update_layout(height=700, margin=dict(l=40, r=40, t=60, b=40), hovermode='closest')
        fig_4.update_xaxes(title_text="Macro Spread (%)", row=2, col=1)
        fig_4.update_xaxes(title_text="Macro Spread (%)", row=2, col=2)
        fig_4.update_yaxes(title_text="Past 90-Day Return (%)", row=1, col=1)
        fig_4.update_yaxes(title_text="Past 90-Day Return (%)", row=2, col=1)
    else:
        fig_4.update_layout(
            title=f"🔎 {active_phases[0][3]} Phase (拡大表示)",
            height=600, margin=dict(l=40, r=40, t=60, b=40), hovermode='closest',
            xaxis_title="Macro Spread (%)",
            yaxis_title="Past 90-Day Return (%)"
        )

    st.plotly_chart(fig_4, width='stretch')